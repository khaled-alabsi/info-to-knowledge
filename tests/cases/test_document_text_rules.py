"""The note-id brackets, the said-twice check, the credential labels and the warning line, as their readers use them."""

from support import harness

from utils import document_text_utils
from utils import finding_text_utils
from utils import tool_call_text_utils


def test_a_range_written_backwards_stays_in_the_text_to_be_named():
    paragraph = document_text_utils.move_bracketed_entry_ids({"text": "A refund is split. [F1.8-F1.4]", "entry_ids": []})
    harness.check("never taken out citing nothing", "[F1.8-F1.4]" in paragraph["text"]
                  and document_text_utils.find_unread_brackets(paragraph["text"]) == ["[F1.8-F1.4]"], str(paragraph))


def test_a_cut_in_an_unread_bracket_is_never_called_no_note_id():
    described = document_text_utils.describe_unread_items("[F6.4, cut-003, F12]")
    harness.check("only the whole frame is named", len(described) == 1 and "F12" in described[0], str(described))


def test_an_authorization_scheme_in_quotes_is_no_value():
    harness.check("a quoted Basic under a mask is the scheme",
                  document_text_utils.is_label_without_value(["Authorization:", '"Basic"']))


def test_a_sentence_wrapped_over_two_lines_is_found_said_again():
    earlier = "The proxy sends the token\nto the provider."
    harness.check("found in the later section", document_text_utils.find_sentences_said_earlier(
        "The proxy sends the token to the provider.", earlier) != [])


def test_a_code_block_said_twice_is_one_sentence_whatever_follows_its_fence():
    """The fence line is no word: glued to the code's last line, "<code> ```" was the document's sentence and no
    section's (lecture-1, 2026-10-14)."""

    once = "```\nfloat(3)\nint(3.9)\nround(3.9)\n```"
    document = f"### First\n\n{once}\n[F1.1]\n\n### Second\n\n{once}\n\nIt returns four.\n"
    harness.check("the code is found said twice, without its fence",
                  document_text_utils.find_repeated_sentences(document) == ["float(3) int(3.9) round(3.9)"],
                  document_text_utils.find_repeated_sentences(document))


def test_a_line_repeated_inside_one_code_block_is_no_repeated_sentence():
    """A workflow file repeats its checkout step in each job, and the chapter was refused for saying it three times
    (lecture-9, 2026-10-21)."""

    workflow = ("```yaml\njobs:\n  test:\n    steps:\n      - uses: actions/checkout@v5\n      - run: hatch test\n"
                "  fmt:\n    steps:\n      - uses: actions/checkout@v5\n      - run: hatch fmt --check\n```")
    harness.check("one block repeating its own line is said once",
                  document_text_utils.find_repeated_sentences(f"### CI\n\n{workflow}\n[F1.1]\n") == [],
                  document_text_utils.find_repeated_sentences(workflow))
    harness.check("the same block under two headings is still said twice",
                  len(document_text_utils.find_repeated_sentences(f"### A\n\n{workflow}\n\n### B\n\n{workflow}\n")) == 1)


def test_a_fixed_line_two_different_code_blocks_share_is_no_repeated_sentence_defect():
    # Defect: two permission dialogs written as their notes' blocks each ended with the dialog's fixed line, and the
    # check refused it as said twice three drafts running (Lecture 7's chapter 1, DEFECTS 586). Fix: a joined block's
    # sentence ends are taken out, so a block stays one sentence; the same block twice is still a repeat
    first = ("```text\nRun the refund check on ledger.py.\nDo you want to proceed?\n1. Yes\n"
             "Esc to cancel. Tab to add instructions.\n```")
    second = ("```text\nRun the gateway report on ledger.py.\nDo you want to proceed?\n1. Yes\n"
              "Esc to cancel. Tab to add instructions.\n```")
    harness.check("two different dialogs sharing their fixed lines are said once each",
                  document_text_utils.find_repeated_sentences(f"### Runs\n\n{first}\n[F1.1]\n\n{second}\n[F2.1]\n") == [],
                  document_text_utils.find_repeated_sentences(f"{first}\n\n{second}"))
    harness.check("the same dialog under two headings is still said twice",
                  len(document_text_utils.find_repeated_sentences(f"### A\n\n{first}\n\n### B\n\n{first}\n")) == 1)


def test_a_sentence_about_the_provided_transcript_is_narration():
    """One word between "the" and "transcript" hid a sentence about the recording (lecture-1, 2026-10-15)."""

    sentence = "The three steps begin with the Gateway login, but the list is not completed in the provided transcript."
    harness.check("the provided transcript is about the recording",
                  document_text_utils.find_narrated_sentences(sentence) != [], sentence)
    harness.check("a product that transcribes calls is still the subject",
                  document_text_utils.find_narrated_sentences("AcmeSync transcribes every support call and stores "
                                                              "the call transcript.") == [], "")


def test_a_warning_with_no_detail_is_named_by_its_code():
    line = finding_text_utils.render_warnings_for_console([{"detail": "a"}, {"code": "B"}])
    harness.check("no empty part in the line", line == "a; B", line)


def test_naming_only_the_later_lines_of_a_span_sets_nothing_aside():
    from utils import document_plan_utils

    entries = [{"id": "S0001", "kind": "speech", "segments": ["S0001", "S0002"], "timestamps": [1.0]}]
    harness.check("one of two lines named: nothing set aside",
                  document_plan_utils.resolve_set_aside(["S0002"], entries) == [], str(document_plan_utils.resolve_set_aside(["S0002"], entries)))
    harness.check("both named: the span is set aside",
                  document_plan_utils.resolve_set_aside(["S0001", "S0002"], entries) == ["S0001"])


def test_a_range_written_backwards_is_described():
    described = document_text_utils.describe_unread_items("[F1.8-F1.4]")
    harness.check("its item named with what is wrong", len(described) == 1 and "backwards" in described[0], str(described))


def test_a_sentence_going_on_after_value_on_a_secret_s_line_is_kept_defect():
    # A slide about a CRC "secret" had its notes' "a random 4-byte value generated at boot" stored as "value [secret]":
    # the words after "value" were hidden with no ":" or "=" marking a label. Now a labelled value is still hidden
    record = {"frame": 1, "facts": ['The 4-byte CRC "secret" is mangled with a random 4-byte value generated at boot.',
                                    "The client secret value: abc123XYZ", "client_secret Value abc123XYZ"]}
    facts = document_text_utils.hide_secrets_in_record(record)["facts"]
    harness.check("the sentence stays whole", facts[0] == record["facts"][0], facts[0])
    harness.check("a value after a label is hidden",
                  facts[1] == "The client secret value: [secret]" and facts[2] == "client_secret Value [secret]", str(facts))


def test_a_code_block_holding_an_ellipsis_line_stays_one_sentence_defect():
    # A terminal output with a "..." line was cut there, and its opening lines, shared with another output, were refused
    # as a sentence said twice (DEFECTS 657). Now a run of marks inside a code block ends no sentence
    text = ("```\n> python run.py\nConnecting.\n```\n\nThen:\n\n```\n> python run.py\nConnecting.\n...\n100%\nDone\n```")
    harness.check("the two outputs are no repeat", document_text_utils.find_repeated_sentences(text) == [],
                  str(document_text_utils.find_repeated_sentences(text)))


def test_a_value_joined_to_the_word_before_it_is_no_label_defect():
    # A fact on a line saying "secret recovery" was stored as "Tag-Length-Value [secret] `[...][value[secret]`": the
    # "value" of a hyphenated term and of code read as a label (DEFECTS 651). Now a joined "value" is no label
    record = {"frame": 1, "facts": ["Tag-Length-Value (TLV) `[tag][value]` helps secret recovery.",
                                    "The client secret value: abc123XYZ"]}
    facts = document_text_utils.hide_secrets_in_record(record)["facts"]
    harness.check("the term and the code stay whole", facts[0] == record["facts"][0], facts[0])
    harness.check("a labelled value is still hidden", facts[1] == "The client secret value: [secret]", facts[1])


def test_a_quoted_bearer_before_a_template_variable_shows_no_secret():
    harness.check("nothing readable", document_text_utils.find_readable_secrets(
        ["Authorization:", '"Bearer', '{{accessToken}}"']) == [])


def test_a_frame_of_an_unknown_image_type_is_refused_by_name():
    import tempfile
    from pathlib import Path

    from utils import frame_message_utils

    frame = Path(tempfile.mkdtemp()) / "frame-001.webp"
    frame.write_bytes(b"RIFF")
    try:
        frame_message_utils.encode_frame(frame)
        answer = "encoded"
    except ValueError as exc:
        answer = str(exc)
    harness.check("never sent as a PNG", "frame-001.webp" in answer, answer)


def test_a_secret_inside_a_web_address_or_a_screenshot_entry_is_hidden_before_it_is_stored():
    token = "Ab3~kL9.qZ7_rT3vB8wN2mL5pH1jD4sF6gY0"
    record = {"urls": [{"url": "https://portal.example.com/cb", "what_it_is": f"the callback with {token}"}],
              "screenshots": [{"part": "the secrets table", "importance": "needed", "why": f"it shows {token}"}]}
    hidden = document_text_utils.hide_secrets_in_record(record)
    harness.check("both entries hold [secret], never the token", token not in str(hidden) and str(hidden).count("[secret]") == 2,
                  str(hidden))


def test_a_batchs_open_questions_written_as_a_numbered_list_are_kept():
    from utils import frame_markdown_utils

    entries = frame_markdown_utils._read_batch_entries(["1. Who owns the retry budget?", "2. Which tenant is meant?"])
    harness.check("both questions", entries == ["Who owns the retry budget?", "Which tenant is meant?"], str(entries))


def test_a_cut_off_address_joined_into_the_longer_one_keeps_its_frames_tags():
    from utils import frame_merge_utils

    entries = [{"id": "F5.1", "kind": "url", "text": "https://acme.example/lo…", "frames": [5], "timestamps": [50.0],
                "tags": ["auth"]},
               {"id": "F9.1", "kind": "url", "text": "https://acme.example/login", "frames": [9], "timestamps": [90.0],
                "tags": ["portal"]}]
    joined = frame_merge_utils._join_cut_off_addresses(entries)
    harness.check("one entry, both frames' tags", len(joined) == 1 and set(joined[0]["tags"]) == {"auth", "portal"},
                  str(joined))


def test_a_detail_written_flush_left_is_the_entrys_detail():
    from utils import frame_markdown_utils

    errors: list[str] = []
    entries = frame_markdown_utils._read_labelled("screenshots", ["- the bar chart", "importance: needed",
                                                                  "why: it holds the totals"], errors)
    harness.check("one entry with its importance", len(entries) == 1 and entries[0].get("importance") == "needed",
                  f"{entries} {errors}")


def test_an_address_with_no_readable_start_stays_its_own_entry():
    """An address read only as "…" has no start to match: folding it into the one other address would be a guess."""

    from utils import frame_merge_utils

    entries = [{"id": "F5.1", "kind": "url", "text": "… | sign-in page", "frames": [5], "timestamps": [50.0], "tags": []},
               {"id": "F9.1", "kind": "url", "text": "https://acme.example/login", "frames": [9], "timestamps": [90.0],
                "tags": []}]
    harness.check("both entries stay", len(frame_merge_utils._join_cut_off_addresses(entries)) == 2)


def test_a_second_look_s_lines_under_any_bullet_are_read_without_it():
    from utils import frame_markdown_utils

    text = ('# Frame 0\n\n## What changed\nan arrow was backwards\n\n## Wrong in the diagram\n* C["Ledger"] --> B\n\n'
            '## Not on this frame\n1. "Export button"\n')
    parsed = frame_markdown_utils.parse_answer(text, is_addition=True)["frames"][0]
    harness.check("the bullet is no part of the line", parsed.get("wrong_diagram_lines") == ['C["Ledger"] --> B']
                  and parsed.get("wrong_parts") == ["Export button"], str(parsed))


def test_tags_written_one_per_line_are_each_a_tag():
    from utils import frame_markdown_utils

    tags = frame_markdown_utils._read_tags(["retry policy", "AcmeSync gateway", "reference"])
    harness.check("three tags", tags == ["retry policy", "AcmeSync gateway", "reference"], str(tags))
    harness.check("a comma list wrapped onto a second line still joins",
                  frame_markdown_utils._read_tags(["retry policy, AcmeSync", "gateway, reference"])
                  == ["retry policy", "AcmeSync gateway", "reference"])


def test_on_screen_is_kept_inside_quotes_and_diagrams():
    """The words are taken out of the writer's own sentences only: a quoted label and a diagram say what the screen said."""

    from utils import section_markdown_utils

    markdown = ('### Settings (T01.1)\nThe page shows the toggle on screen. The button "Show on screen keyboard" opens it. '
                '[F1.1]\n\n```mermaid\nflowchart LR\n    A["Keyboard on screen"] --> B\n```\n[F1.2]\n')
    part = section_markdown_utils.split_chapter_markdown(markdown)[0]
    harness.check("the writer's own words lose it", "the toggle." in part, part)
    harness.check("the quoted label and the diagram keep it",
                  '"Show on screen keyboard"' in part and '"Keyboard on screen"' in part, part)


def test_a_sequence_diagram_s_messages_are_counted_as_arrows():
    from utils import mermaid_text_utils

    sequence = ("sequenceDiagram\n    Jonas->>Gateway: sign in\n    Gateway-->>Jonas: token\n    Jonas-)Gateway: ping\n"
                "    Gateway-xJonas: refused\n    Jonas->Gateway: plain\n")
    harness.check("five messages, five arrows", mermaid_text_utils.count_arrows(sequence) == 5,
                  str(mermaid_text_utils.count_arrows(sequence)))
    harness.check("a flowchart counts as before",
                  mermaid_text_utils.count_arrows("flowchart LR\n    A --> B\n    B -.-> C\n    C --- D\n") == 3)


def test_a_note_number_the_model_sent_as_true_or_a_decimal_is_refused():
    from utils import reading_tool_utils

    for replaces in (True, 2.5):
        notes, reply = reading_tool_utils.update_notes(["first", "second"], "new", replaces, 1_000)
        harness.check(f"replaces={replaces!r} is refused", notes is None and "replaces" in reply, f"{notes} {reply}")
    notes, _ = reading_tool_utils.update_notes(["first", "second"], "new", "2", 1_000)
    harness.check("a number written as text still names its note", notes == ["first", "new"], str(notes))


def test_on_screen_is_taken_out_of_an_answer_wrapped_whole_in_a_fence():
    from utils import section_markdown_utils

    markdown = "```markdown\n### Login (T01.1)\nThe form on screen asks for a name. [F3.1]\n```\n"
    part = section_markdown_utils.split_chapter_markdown(markdown)[0]
    harness.check("the fence is taken off first, then the words", "The form asks for a name." in part, part)


def test_a_frame_line_written_with_a_leading_dot_slash_gets_its_file():
    from utils import section_markdown_utils

    replaced = section_markdown_utils.replace_cut_ids('![a](./cut-03 "c")', {"cut-03": "images/cuts/cut-03.png"})
    harness.check("the cut's file replaces it", replaced == '![a](images/cuts/cut-03.png "c")', replaced)


def test_a_value_option_followed_by_another_option_is_refused_for_its_value():
    from models.ui_errors import SlashCommandError
    from utils import slash_command_utils

    try:
        slash_command_utils.parse_slash_command("/process 1 --from --dry-run")
        message = "no error"
    except SlashCommandError as error:
        message = str(error)
    harness.check("--from needs a number", "--from needs a number" in message, message)


def test_an_asymmetric_box_is_no_unclosed_bracket():
    from utils import mermaid_text_utils

    problems = mermaid_text_utils.find_syntax_problems('flowchart LR\n    A>"Flag"] --> B\n')
    harness.check("A>\"Flag\"] draws", problems == [], str(problems))
    harness.check("a bracket left open is still named",
                  mermaid_text_utils.find_syntax_problems('flowchart LR\n    A["Flag" --> B\n') != [])


def test_class_er_and_circle_links_are_counted_as_arrows():
    from utils import mermaid_text_utils

    harness.check("class links", mermaid_text_utils.count_arrows("classDiagram\n    A <|-- B\n    A *-- C\n    A ..> D\n") == 3)
    harness.check("ER links", mermaid_text_utils.count_arrows(
        "erDiagram\n    CUSTOMER ||--o{ ORDER : places\n    ORDER ||--|{ LINE : holds\n") == 2)
    harness.check("circle and cross heads", mermaid_text_utils.count_arrows("flowchart LR\n    A --o B\n    B --x C\n") == 2)


def test_a_bare_label_never_takes_an_id_the_diagram_already_uses():
    from utils import mermaid_text_utils

    named = mermaid_text_utils.name_bare_nodes('flowchart LR\n    n1[Gateway] --> "Proxy"\n')
    harness.check("Proxy gets an id of its own", 'n1["Proxy"]' not in named and '["Proxy"]' in named, named)


def test_an_indented_closing_fence_closes_the_fence_for_every_reader():
    from utils import section_markdown_utils

    markdown = ("### Settings (T01.1)\n```\nretry: 3\n  ```\nThe toggle on screen sets it. [F1.1]\n")
    part = section_markdown_utils.split_chapter_markdown(markdown)[0]
    harness.check("the sentence after the fence loses it", "The toggle sets it." in part, part)


def test_a_text_call_with_null_arguments_is_a_call():
    call = tool_call_text_utils.read_text_call('{"name": "submit_final", "arguments": null}')
    harness.check("read as a call with no arguments", call == {"name": "submit_final", "args": {}, "id": ""}, str(call))


def test_an_answer_written_as_bare_arguments_is_the_submit_call():
    """A summary was written twice as bare JSON in the reply, once after "submit_final" and a thinking block
    (pdf-real-fw9)."""

    bare = '{\n  "title": "Orders ship in March",\n  "summary": []\n}'
    harness.check("bare JSON is the tool's arguments",
                  tool_call_text_utils.read_bare_arguments(bare, "submit_final") == {"title": "Orders ship in March", "summary": []})
    harness.check("after the tool's name, a thinking block and a fence",
                  tool_call_text_utils.read_bare_arguments(
                      "submit_final <details><summary>Think</summary>I write the summary.</details>\n```json\n" + bare + "\n```",
                      "submit_final") == {"title": "Orders ship in March", "summary": []})
    harness.check("prose and a named call are not bare arguments",
                  tool_call_text_utils.read_bare_arguments("I will write the summary now.", "submit_final") is None
                  and tool_call_text_utils.read_bare_arguments('{"name": "submit_final", "arguments": {}}', "submit_final") is None)


def test_a_plus_between_flowchart_nodes_is_written_as_mermaid_s_and():
    """Mermaid joins an arrow's sources with "&" and has no "+": the wiki's Calvin cycle wrote
    `CO2["Carbon Dioxide"] + RuBP["Ribulose 1,5-bisphosphate"] --> RuBisCO`, and the published block did not render.
    A "+" inside a label or on an edge's text stays."""

    from utils import mermaid_text_utils

    source = ('flowchart TD\nCO2["Carbon Dioxide"] + RuBP["RuBP"] --> RuBisCO["RuBisCO"]\n'
              'PGA + ATP + NADPH --> G3P["G3P"]\nA -->|2 O2 + RuBP| B["NADP+ + H+"]')
    repaired = mermaid_text_utils.join_plus_nodes(source).splitlines()
    harness.check("the sources are joined by &", repaired[1] == 'CO2["Carbon Dioxide"] & RuBP["RuBP"] --> RuBisCO["RuBisCO"]'
                  and repaired[2] == "PGA & ATP & NADPH --> G3P[\"G3P\"]", str(repaired))
    harness.check("a plus in an edge's text and a label stays", repaired[3] == 'A -->|2 O2 + RuBP| B["NADP+ + H+"]', str(repaired))


def test_a_sentence_whose_subject_is_a_slide_is_narration_defect():
    # Defect: "A slide lists command examples" and "The title slide lists the details" reached a published chapter.
    # Fix: a sentence opening on a slide, with any article and one word before it, is narration; a slide deck is not.
    for sentence in ("A slide lists the Gateway's commands.", "The title slide lists the presenter's details:",
                     "The Gateway's options are listed on a slide."):
        harness.check(f"{sentence!r} is narration", document_text_utils.find_narrated_sentences(sentence) != [], sentence)
    harness.check("a slide deck is the subject's", document_text_utils.find_narrated_sentences(
        "A slide deck is exported to PDF by AcmeSync.") == [])


def test_a_full_name_the_notes_give_a_role_is_found_and_a_company_is_not_defect():
    # A chapter named the presenter in full where write.md asks for the role, and nothing refused it (DEFECTS 292);
    # now the full names the notes give a person's role are known, and a company "by" a slide names is not one
    notes = ["The presenter Jonas Weber shows the ledger.", "Referent: **Mara Lind**", "Ida Holm is the speaker.",
             "The logo is by Gateway Systems."]
    harness.check("each role's full name is found, the company is not",
                  document_text_utils.list_role_names(notes) == {"Jonas Weber", "Mara Lind", "Ida Holm"},
                  str(document_text_utils.list_role_names(notes)))


def test_a_block_line_with_other_numbers_than_its_screen_line_is_named_defect():
    # A chapter copied a boot log with "RSSI: 0" where the notes' screen text wrote "RSSI: 100"; the line is now named
    # beside the note's own, and a line the notes hold, or one with no numbers, is not
    from utils import document_text_utils
    screen = ["```\nGateway: PORT: 8080 RETRIES: 3\nGateway ready\n```"]
    blocks = ["Gateway: PORT: 80 RETRIES: 3\nGateway ready\nGateway started"]
    harness.check("the changed line is named with the note's line",
                  document_text_utils.find_changed_value_lines(blocks, screen)
                  == [("Gateway: PORT: 80 RETRIES: 3", "Gateway: PORT: 8080 RETRIES: 3")],
                  str(document_text_utils.find_changed_value_lines(blocks, screen)))
    harness.check("a line copied as it stands passes",
                  not document_text_utils.find_changed_value_lines(["Gateway: PORT: 8080 RETRIES: 3"], screen))


def test_a_build_step_line_mentioning_a_password_hides_nothing_under_it_defect():
    # A step's line summed up "a password prompt overlay and an updated row", and the program hid the next row's
    # device name as a secret (DEFECTS 345); a step's own line names no credential field
    from utils import document_text_utils

    outline = ('- step 2 of the screen of frame 4: shows the Gateway window with a password prompt overlay\n'
               '  - row update in "Paired Devices": "AcmeSync Headset 7" now shows "60%"')
    harness.check("the row's name under a step line stays", "AcmeSync Headset 7" in
                  document_text_utils.hide_secret_tokens(outline), document_text_utils.hide_secret_tokens(outline))


def test_a_he_or_she_the_source_never_used_is_written_they_defect():
    # A chapter wrote "Jonas notes he will add the region" where the page says "I will add"; write.md forbids it and no
    # check refuses it (the owner removed that check). Now the program writes "they", and a pronoun the source uses stays
    from utils import document_text_utils

    written = document_text_utils.write_unheard_pronouns_as_they(
        text="Jonas notes he will add the region [S0032]. She asked why. His team agreed. The theme held. He is here.",
        source_text="Jonas: Thanks, I will add the region.")
    harness.check("he, she and his become they and their where the verb reads the same", written ==
                  "Jonas notes they will add the region [S0032]. They asked why. Their team agreed. The theme held. "
                  "He is here.", written)
    kept = document_text_utils.write_unheard_pronouns_as_they(text="He will add it.", source_text="and he said so")
    harness.check("a he the source uses stays", kept == "He will add it.", kept)


def test_a_count_above_its_table_s_rows_is_named_and_a_part_count_is_not_defect():
    # A chapter wrote "82 incidents" over its own table of 80 (DEFECTS 519). Now a count of a table's rows, named by its
    # first column, above the rows it holds is named; a smaller count, a part of the rows, is not
    from utils import document_text_utils
    from utils import section_markdown_utils

    rows = "\n".join(f"|INC-{number}|ledger timeout|" for number in range(3))
    table = f"|**Incident**|**Cause**|\n|---|---|\n{rows}\n[S0008]"
    too_many = section_markdown_utils.parse_section(f"The gateway recorded four incidents in Q1 [S0008].\n\n{table}")
    part = section_markdown_utils.parse_section(f"Two incidents were ledger timeouts [S0008].\n\n{table}")
    harness.check("four incidents over three rows is named", document_text_utils.find_counts_above_table_rows(too_many)
                  == ["four incidents (the table holds 3)"], str(document_text_utils.find_counts_above_table_rows(too_many)))
    harness.check("two of three is a part, not named", document_text_utils.find_counts_above_table_rows(part) == [], "")


def test_a_unit_the_notes_never_give_a_number_is_named_defect():
    # A chapter wrote "setting it to 60 seconds" for `--retry-budget 60`, which counts retries (DEFECTS 516). A number with a
    # time unit its notes give with another unit or none is named; the notes' own unit, or a unit in a key, is not
    from utils import document_text_utils

    notes = "Run `acmesync tenant set --retry-budget 60` to change it. lifetime_seconds: 3600. A wait of 2 s."
    harness.check("a unit the notes never give is named",
                  document_text_utils.find_units_unstated("such as setting it to 60 seconds.", notes) == ["60 seconds"],
                  str(document_text_utils.find_units_unstated("such as setting it to 60 seconds.", notes)))
    harness.check("the notes' own unit, another way of writing it, is not named",
                  document_text_utils.find_units_unstated("The wait is 2 seconds.", notes) == [], "")
    harness.check("a unit the notes give in a key is not named",
                  document_text_utils.find_units_unstated("A token lives 3600 seconds.", notes) == [], "")
    harness.check("a number the notes never hold is left alone",
                  document_text_utils.find_units_unstated("It took 45 minutes.", notes) == [], "")


def test_a_unit_a_cited_fact_gives_is_given_by_the_notes_defect():
    # A section wrote a fact's own "thought for 2s" and was refused as a unit no note gives: the check read the speech
    # and the markdown notes, never the facts (DEFECTS 590). Every note the section cites now counts
    from support import fixtures as fx
    from models.section_judging_context import SectionJudgingContext
    from services import call_log_service, document_file_service
    from validators import document_validator

    markdown = "The gateway thought for 2s before it answered. [F7.3]\n"
    paths = fx.make_paths()
    call_log_service.write_call_log(call_log_service.build_call_log_path(paths, "heading-001-1"),
                                    {"response": {"outcome": "completed", "parsed": {"markdown": markdown},
                                                  "finishReason": "stop"}})
    document_file_service.write_section(paths, "heading-001-1", markdown)
    scope = SectionJudgingContext(entry_ids=["F7.3", "S0002"], required_ids=[],
                                  speech_texts=["We tried 2 gateways first."], screen_texts=[],
                                  note_texts=["The gateway thought for 2s before it answered.", "We tried 2 gateways first."])
    report = document_validator.judge_section(paths, "heading-001-1", scope, [])
    codes = [finding.code for finding in report.findings]
    harness.check("the fact's own unit is no unit the notes never give", "DOCUMENT_UNIT_UNSOURCED" not in codes, str(codes))


def test_minutes_written_m_inside_a_duration_are_minutes_defect():
    # A chapter wrote "4 minutes 24 seconds" for a cost report's "4m 24s" and was refused as adding a unit (DEFECTS 589).
    # An "m" between hours and seconds is minutes; a bare "m" stays no unit
    from utils import document_text_utils

    harness.check("'4m 24s' gives 4 minutes",
                  document_text_utils.find_units_unstated("It ran 4 minutes 24 seconds.", "Total duration: 4m 24s") == [],
                  str(document_text_utils.find_units_unstated("It ran 4 minutes 24 seconds.", "Total duration: 4m 24s")))
    harness.check("'1h 4m' gives 4 minutes",
                  document_text_utils.find_units_unstated("It ran 1 hour 4 minutes.", "Took 1h 4m") == [], "")
    harness.check("a bare '4m' is still no minute",
                  document_text_utils.find_units_unstated("It is 4 minutes away.", "A cable of 4m.") == ["4 minutes"], "")


def test_a_list_repeating_its_section_s_paragraph_is_named_defect():
    # A chapter wrote a slide's points as a paragraph and again as the slide's list (DEFECTS 637, 26 of 43 public lists).
    # Now such a list is named with the paragraph it repeats; a list adding to its paragraph is not
    from utils import document_text_utils

    paragraph = {"kind": "paragraph", "text": "The gateway retries a failed call three times, then queues the request "
                                              "for the nightly batch and alerts the operator. [F1.1]"}
    repeating = {"kind": "list", "text": "- Retries a failed call three times\n- Queues the request for the nightly "
                                         "batch\n- Alerts the operator\n[F1.2]"}
    adding = {"kind": "list", "text": "- Timeout of thirty seconds per attempt\n- Backoff doubling after each failure\n"
                                      "[F1.3]"}
    found = document_text_utils.find_lists_restating_prose([paragraph, repeating, adding])
    harness.check("the repeating list is named with its paragraph",
                  [lst.splitlines()[0] for lst, _ in found] == ["- Retries a failed call three times"], str(found))


def test_a_rate_its_notes_never_give_per_that_time_is_named_defect():
    # A chapter wrote a dataset's "several kilometers" resolution as "several kilometers per hour", joining the hourly
    # series beside it; the unit check read only numbers (DEFECTS 635). A rate whose word the notes hold is checked too
    from utils import document_text_utils

    notes = "The archive holds hourly records at a resolution of several kilometers."
    harness.check("a rate the notes never give is named",
                  document_text_utils.find_units_unstated("A resolution of several kilometers per hour.", notes)
                  == ["kilometers per hour"],
                  str(document_text_utils.find_units_unstated("A resolution of several kilometers per hour.", notes)))
    harness.check("'50 retries in one minute' gives 'per minute'",
                  document_text_utils.find_units_unstated("A budget of 50 retries per minute.",
                                                          "A budget of 50 retries in one minute.") == [], "")
    harness.check("'requests/s' gives 'per second'",
                  document_text_utils.find_units_unstated("Up to 10 requests per second.", "10 requests/s") == [], "")
    harness.check("a word the notes never hold is left alone",
                  document_text_utils.find_units_unstated("Two frames per second.", notes) == [], "")


def test_a_figure_fact_from_the_page_alone_is_a_page_fact_defect():
    # A figure's facts restated its page's sentences reworded, which a word-for-word match let through (DEFECTS 529). A
    # fact with half its words from the page and none on the figure is the page's; one naming a figure label is not
    from validators import item_validator

    page = item_validator._list_fact_words("A call enters through the edge proxy, which checks the partner's signature "
                                           "with SHA-256.")
    figure = item_validator._list_fact_words("Client Gateway Keyhole")
    harness.check("the reworded page sentence is a page fact", item_validator._is_page_fact(
        "An edge proxy checks the partner's signature using SHA-256 before accepting the call.", page, figure), "")
    harness.check("a fact naming the figure's box is not", not item_validator._is_page_fact(
        "The Gateway is the edge proxy that checks the partner's signature.", page, figure), "")


def test_a_figure_outline_line_quoting_its_page_leaves_the_outline_defect():
    # A repair wrote two page sentences into a figure's outline as text on the figure (DEFECTS 529). An outline line whose
    # quote of four words or more is the page's and holds no word read on the figure goes; a box's label stays
    from services import frame_records_service
    from validators import item_validator

    record = {"screen": '- box "Gateway": centre\n- title text: "A call enters through the edge proxy, which checks '
                        'the signature."\n- box "Keyhole": right'}
    readers = (item_validator._read_figure_words, frame_records_service.read_frame_medium,
               frame_records_service.read_frame_speech_before)
    item_validator._read_figure_words = lambda paths, slug: {"gateway", "keyhole", "client"}
    frame_records_service.read_frame_medium = lambda paths, slug: "pdf-figure"
    frame_records_service.read_frame_speech_before = lambda paths, slug: ("A call enters through the edge proxy, which "
                                                                          "checks the partner's signature with SHA-256.")
    try:
        screen = item_validator.drop_figure_page_outline_lines(None, "frame-000", record)["screen"]
    finally:
        (item_validator._read_figure_words, frame_records_service.read_frame_medium,
         frame_records_service.read_frame_speech_before) = readers
    harness.check("the page sentence leaves, both box lines stay",
                  screen == '- box "Gateway": centre\n- box "Keyhole": right', screen)


def test_a_figure_fact_s_default_no_source_holds_leaves_the_fact_defect():
    # A settings form showing "3600 s" was noted "The default token lifetime is 3600 seconds", and the chapter printed
    # the default (DEFECTS 512). A word stating how a value stands goes where no source holds it; one the page holds stays
    from services import frame_records_service
    from validators import item_validator

    record = {"facts": ["The default token lifetime is 3600 seconds.", "The audience is required by the gateway.",
                        "The refresh window is 300 seconds by default."], "screen": '- field "Token lifetime": "3600 s"'}
    readers = (item_validator._read_figure_words, frame_records_service.read_frame_medium,
               frame_records_service.read_frame_speech_before)
    item_validator._read_figure_words = lambda paths, slug: {"token", "lifetime", "3600", "audience"}
    frame_records_service.read_frame_medium = lambda paths, slug: "pdf-figure"
    frame_records_service.read_frame_speech_before = lambda paths, slug: "An audience is required on every call."
    try:
        facts = item_validator.drop_figure_unsourced_standing_words(None, "frame-000", record)["facts"]
    finally:
        (item_validator._read_figure_words, frame_records_service.read_frame_medium,
         frame_records_service.read_frame_speech_before) = readers
    harness.check("both defaults go with the values kept, the page's required stays",
                  facts == ["The token lifetime is 3600 seconds.", "The audience is required by the gateway.",
                            "The refresh window is 300 seconds."], str(facts))


def test_a_figure_fact_saying_only_how_it_looks_leaves_the_notes_defect():
    # A three-box diagram's facts held its boxes' size, fill and outline, which the chapter printed as a paragraph
    # (DEFECTS 504). A figure fact made of looks goes while another remains; one naming what the figure states stays
    from services import frame_records_service
    from validators import item_validator

    record = {"facts": ["All three boxes share the same dimensions, light blue background fill, and dark blue outline.",
                        "A call goes from the Client through the Gateway to Keyhole."]}
    medium = frame_records_service.read_frame_medium
    frame_records_service.read_frame_medium = lambda paths, slug: "pdf-figure"
    try:
        facts = item_validator.drop_figure_look_facts(None, "frame-000", record)["facts"]
    finally:
        frame_records_service.read_frame_medium = medium
    harness.check("the looks fact leaves, the flow stays",
                  facts == ["A call goes from the Client through the Gateway to Keyhole."], str(facts))


def test_a_chapter_repeated_whole_ends_before_its_second_copy_defect():
    # A chapter written as text repeated itself whole until it was stopped, and the draft checked kept a second copy,
    # refused for a heading standing twice (DEFECTS 531). The draft now ends at the first heading written again
    from utils import section_markdown_utils

    copy = "### Abstract (T01.1)\n\nA [S1].\n\n### Preface (T01.2)\n\nB [S2]."
    harness.check("the draft ends before the second copy",
                  section_markdown_utils.read_chapter_draft(f"{copy}\n\n{copy}\n\n### Abstract (T01.1)") == copy,
                  section_markdown_utils.read_chapter_draft(f"{copy}\n\n{copy}"))
    harness.check("a chapter written once is kept whole", section_markdown_utils.read_chapter_draft(copy) == copy, "")


def test_a_chapter_written_as_text_ends_where_its_call_closes_defect():
    # A reply wrote its chapter as text, closed its call and thought on; the tags and the thinking were checked as blocks
    # of the chapter (DEFECTS 527). The draft is read by the chapter's contract, which no closing markup line belongs to
    from utils import section_markdown_utils

    reply = "### Retry policy (T03.1)\n\nA call is retried [S0011].\n</parameter>\n</function>\n</tool_call>thought\nDraft:"
    harness.check("the draft ends at the call's close",
                  section_markdown_utils.read_chapter_draft(reply) == "### Retry policy (T03.1)\n\nA call is retried [S0011].",
                  section_markdown_utils.read_chapter_draft(reply))
    fenced = "### Page (T01.1)\n\nThe template [S1]:\n\n```html\n<div>\n</div>\n```\n[S1]"
    harness.check("a closing tag inside a code block is the chapter's own",
                  section_markdown_utils.read_chapter_draft(fenced) == fenced, section_markdown_utils.read_chapter_draft(fenced))


def test_a_looped_chapter_draft_drops_what_stands_before_its_first_heading_and_any_other_top_heading_defect():
    # The circle of DEFECTS 230, 527 and 531: each run, a looped chapter ran on past itself in one more way. Read by the
    # chapter's contract, thinking before the first listed heading and a heading the chapter never lists end it too
    from utils import section_markdown_utils

    reply = "Let me draft it.\n### Abstract (T01.1)\n\nA [S1].\n\n#### Detail\n\nB [S1].\n\n## Plan\n\nI will now submit."
    harness.check("the chapter alone, its own level-4 subheading kept",
                  section_markdown_utils.read_chapter_draft(reply) == "### Abstract (T01.1)\n\nA [S1].\n\n#### Detail\n\nB [S1].",
                  section_markdown_utils.read_chapter_draft(reply))


def test_a_number_the_notes_state_as_another_quantity_is_no_row_count_defect():
    # "50 attempts per minute" over a table of 4 attempts was named, though the notes give 50 as the retry budget, "50
    # retries in one minute" (DEFECTS 526). A number the notes state only as another quantity is no count of the rows
    from utils import document_text_utils
    from utils import section_markdown_utils

    table = "|**Attempt**|**Wait**|\n|---|---|\n|1|none|\n|2|2 s|\n|3|4 s|\n|4|8 s|\n[S0014]"
    budget = section_markdown_utils.parse_section(f"The budget is 50 attempts per minute per tenant [S0013].\n\n{table}")
    notes = "Once a tenant spends 50 retries in one minute, new failures are returned at once."
    harness.check("a budget the notes state as retries is not named",
                  document_text_utils.find_counts_above_table_rows(budget, notes) == [],
                  str(document_text_utils.find_counts_above_table_rows(budget, notes)))
    harness.check("a count the notes give as the same noun is still named",
                  document_text_utils.find_counts_above_table_rows(budget, "The client makes 50 attempts.") != [], "")
    harness.check("a count no note holds is still named", document_text_utils.find_counts_above_table_rows(budget, "") != [], "")


def test_a_slide_sentence_note_restating_an_earlier_fact_is_named_defect():
    # A package page's own sentence, held in a markdown note, was written as a paragraph after the facts that said it
    # (DEFECTS 637); the request now names it beside the fact it restates
    entries = [{"id": "F9.1", "kind": "facts", "text": "Gateway is an open-source package for refund routing based on ledgers."},
               {"id": "F9.2", "kind": "facts", "text": "The package runs on the server using queues."},
               {"id": "F9.5", "kind": "markdown", "text": "**Gateway**\n\nGateway is a package for refund routing based on "
                                                         "ledgers. It runs on the server using queues."},
               {"id": "F9.6", "kind": "markdown", "text": "| field | value |\n|---|---|\n| owner | Jonas |"}]
    restating = document_text_utils.list_restating_entries(entries)
    harness.check("the slide's sentence is named as the fact, the table is not", restating == {"F9.5": "F9.1"}, str(restating))


def test_a_looped_chapter_ends_at_a_heading_of_another_chapter_defect():
    # A chapter's looped reply wrote the next chapter's two headings, then repeated them, and was checked as 4 sections
    # for 2 headings (DEFECTS 636). Read with the chapter's own ids, it ends at the first heading of another chapter
    from utils import section_markdown_utils

    own = "### Lessons (T06.1)\n\nSimple code held [F71.2].\n\n### Next steps (T06.2)\n\nBalconies first [F72.1]."
    reply = f"{own}\n\n### Funding (T07.1)\n\nA grant [F90.1].\n\n### Questions (T07.2)\n\nAsked [F91.1]."
    harness.check("the chapter ends where another chapter's heading starts",
                  section_markdown_utils.read_chapter_draft(reply, {"T06.1", "T06.2"}) == own,
                  section_markdown_utils.read_chapter_draft(reply, {"T06.1", "T06.2"}))
    harness.check("without the ids it is read as before", "T07.1" in section_markdown_utils.read_chapter_draft(reply), "")


def test_the_writer_is_told_an_open_point_rests_on_a_note_that_leaves_it_open_defect():
    # A chapter wrote "the timeline for resolving missing regional data is not specified", cited to a note about the
    # prototype running on the device (DEFECTS 630). The writer is now told an open point rests on a note leaving it open
    import config

    instruction = " ".join((config.PLAYBOOK_ROOT / "agent-context" / "instructions" / "_shared" / "write.md").read_text(encoding="utf-8").split())
    harness.check("the rule stands beside the marked lines", "An open point rests on a note that leaves the point open"
                  in instruction and "never write one from what no note says" in instruction, "")

