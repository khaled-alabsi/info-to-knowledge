"""A frame's accepted notes live in its own item of `02_frames.json`, the one file of the frames.

Phase 2 lists the frames and phase 3 fills in what each one taught; one file holds both, so a reader opens one place
and the two can never disagree about which frames exist. Phase 3 still writes each frame the moment it is accepted.
"""

from support import fixtures as fx
from support import harness

from models.pipeline_errors import MissingPhaseInput
from models import output_contract
from services import frame_records_service
from services import phase_file_service
from utils import document_text_utils

ITEMS = [{"slug": f"frame-{index:03d}", "index": index, "timestamp": index * 5.0} for index in range(3)]


def _write_frames(paths) -> None:
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": ITEMS})


def test_an_accepted_frame_is_written_into_its_own_item_of_the_frames_file():
    paths = fx.make_paths()
    _write_frames(paths)
    frame_records_service.save_frame_record(paths, ITEMS[1], {"facts": ["A token lives 30 minutes."]}, ["frame-000"])
    items = phase_file_service.read_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"])["items"]
    harness.check("the notes sit in frame 1's item, and the other items have none",
                  items[1].get("record") == {"facts": ["A token lives 30 minutes."]} and "record" not in items[0],
                  str(items))
    harness.check("the frames it was built from are kept beside it", items[1].get("builtFrom") == ["frame-000"], str(items))
    harness.check("no second file of frame notes is written",
                  not (paths.output_root / "03_frame_analyses.json").exists())
    records = frame_records_service.read_frame_records(paths)
    harness.check("reading gives back one entry per accepted frame, in the shape the later phases read",
                  records == [{"slug": "frame-001", "index": 1, "timestamp": 5.0, "builtFrom": ["frame-000"],
                               "facts": ["A token lives 30 minutes."]}], str(records))


def test_a_new_pass_clears_every_frames_notes_and_keeps_the_frames():
    paths = fx.make_paths()
    _write_frames(paths)
    frame_records_service.save_frame_record(paths, ITEMS[0], {"facts": ["a"]}, [])
    frame_records_service.clear_all_frame_records(paths)
    items = phase_file_service.read_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"])["items"]
    harness.check("the frames stay, their notes are gone",
                  [item["slug"] for item in items] == ["frame-000", "frame-001", "frame-002"]
                  and not any("record" in item for item in items), str(items))


def test_a_secret_value_copied_from_the_screen_is_hidden_before_the_record_is_stored():
    """A frame showed a newly created client secret, and its value was copied into the notes of ten frames. A token
    that mixes upper and lower case, digits and one of ~ . _ is a generated secret, never an id: it is hidden. A GUID
    (an application or tenant id), a web address and ordinary words are left as they were."""

    secret = "Qx7~aB9.cD_ef1GhIjK2lMnO3pQrS4tUvW5xYz"
    guid = "3f2b8c1e-4d5a-4b6c-9e7f-0a1b2c3d4e5f"
    screen = (f'- the app registration page: white\n  - field "Value" = "{secret}": middle of the secrets table\n'
              f'  - field "Application (client) ID" = "{guid}": top')
    record = {"screen": screen, "facts": [f"The secret {secret} expires in two years."],
              "speech_visual_alignment": "", "urls": ["https://portal.example.com/app/overview | the portal"]}
    hidden = document_text_utils.hide_secrets_in_record(record)
    harness.check("the secret's value becomes [secret] wherever it was copied, the screen's outline included",
                  'field "Value" = "[secret]"' in hidden["screen"]
                  and hidden["facts"] == ["The secret [secret] expires in two years."], str(hidden))
    token_part = "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiIsImtpZCI6Ik1uQ19W"
    harness.check("a token segment (base64 JSON, always starting eyJ) is hidden even without a dot",
                  document_text_utils.hide_secrets_in_record({"facts": [f"id_token {token_part}"]})
                  == {"facts": ["id_token [secret]"]})
    harness.check("an id, a web address and plain text stay as they were",
                  guid in hidden["screen"] and hidden["urls"] == record["urls"], str(hidden))
    entry = {"url": "https://portal.example.com/app/overview", "what_it_is": "the portal", "how_it_appears": "a link"}
    harness.check("a web address stored as its entry stays as it was",
                  document_text_utils.hide_secrets_in_record({"urls": [entry]})["urls"] == [entry])
    names = ["test_Refund3Parts_whenLimitReached_thenRejected", "B7_navigate_to_orderPage_over_top_navi_PK_Online_Auto",
             "de.acme.gateway.shared.Order2ServiceImpl_v3", "AC_ME_STG_ORDER_EK_F120_TAB.AUFTRAGSAUFschlag_SATZ",
             "202609_ACM_acmeKR_IST_Systeme_Schnittstellen_Uebersicht"]
    kept = document_text_utils.hide_secrets_in_record({"facts": [f"The test {name} runs." for name in names]})
    harness.check("a code name of the same shape (a method or a class with a digit in it) is never taken for a secret",
                  kept["facts"] == [f"The test {name} runs." for name in names], str(kept))
    no_tilde = "Xk9.qZ7_rT3vB8wN2mL5pH1jD4sF6gY0cR"
    harness.check("and a generated value without ~ is still hidden",
                  document_text_utils.hide_secrets_in_record({"facts": [no_tilde]}) == {"facts": ["[secret]"]})


def test_a_short_value_beside_a_key_named_as_a_credential_is_hidden():
    screen = ("- table of environment variables: middle\n"
              "  - row 1: `SHOP_MODE` / `dev`\n"
              "  - row 2: `ORDERS_TECHNICAL_PASSWORD` / `K(7wq=Z2`\n"
              "  - row 3: `ORDERS_API_KEY` / `b41c7e20-55aa-4e1f`\n"
              "  - row 4: `ACME_CLIENT_KEY_BASE64` / `TFMwdExTMUNSVWRKVGlCUQ`\n"
              '  - field "Client secret" = "s3cr3t-v4lue"\n'
              '  - row 6: Name "ORDERS_SERVICE_SECRET", Value "Zq(4>wP1"\n'
              '  - dropdown field: "ConfigMap/Secret: Select a resource"\n'
              "  - row 5: `ORDERS_TECHNICAL_USERNAME` / `XB4TEST`\n"
              '  - button "Add from ConfigMap or Secret": below the table')
    hidden = document_text_utils.hide_secrets_in_record({"screen": screen})["screen"]
    harness.check("a short password, an API key, a client key and a client secret beside their names become [secret]",
                  "`ORDERS_TECHNICAL_PASSWORD` / `[secret]`" in hidden and "`ORDERS_API_KEY` / `[secret]`" in hidden
                  and "`ACME_CLIENT_KEY_BASE64` / `[secret]`" in hidden
                  and 'field "Client secret" = "[secret]"' in hidden
                  and 'Name "ORDERS_SERVICE_SECRET", Value "[secret]"' in hidden, hidden)
    harness.check("a value beside a name that is no credential, and a label holding the word, stay as they were",
                  "`SHOP_MODE` / `dev`" in hidden and "`ORDERS_TECHNICAL_USERNAME` / `XB4TEST`" in hidden
                  and 'button "Add from ConfigMap or Secret": below the table' in hidden
                  and '"ConfigMap/Secret: Select a resource"' in hidden, hidden)


def test_a_value_on_the_line_under_a_credential_named_row_is_hidden():
    screen = ("- table of variables: middle\n"
              '  - table row "ORDERS_SERVICE_SECRET":\n'
              '    - field "Value": "Zq(4>wP1"\n'
              '  - table row "ORDERS_TECHNICAL_USERNAME":\n'
              '    - field "Value": "XB4TEST"\n'
              '  - table row "ORDERS_API_KEY":\n'
              '    - field "Value": `b41c7e20-55aa-4e1f`')
    hidden = document_text_utils.hide_secrets_in_record({"screen": screen})["screen"]
    harness.check("the value one level under a row named as a credential becomes [secret]",
                  '"ORDERS_SERVICE_SECRET":\n    - field "Value": "[secret]"' in hidden
                  and '"ORDERS_API_KEY":\n    - field "Value": `[secret]`' in hidden, hidden)
    harness.check("the value under a row that is no credential stays", 'field "Value": "XB4TEST"' in hidden, hidden)


def test_notes_stored_with_a_secret_are_read_back_with_it_hidden():
    paths = fx.make_paths()
    _write_frames(paths)
    frames_file = phase_file_service.read_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"])
    frames_file["items"][1]["record"] = {"screen": '- table row "ORDERS_SERVICE_SECRET":\n  - field "Value": "Zq(4>wP1"'}
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], frames_file)
    screen = frame_records_service.read_frame_records(paths)[0]["screen"]
    harness.check("notes stored before the hiding rule reach later phases with the value hidden",
                  '"[secret]"' in screen and "Zq(4" not in screen, screen)


def test_a_value_labelled_value_on_a_credential_named_line_is_hidden_in_any_quoting():
    lines = ["- row: `Name: ORDERS_SERVICE_SECRET`, `Value: Zq(4>wP1`",
             "- row: Name ORDERS_API_KEY | Value b41c7e20-55aa",
             "- row: `Name: ORDERS_TECHNICAL_USERNAME`, `Value: XB4TEST`"]
    hidden = document_text_utils.hide_secrets_in_record({"screen": "\n".join(lines)})["screen"].split("\n")
    harness.check("what follows the value label on a credential's line becomes [secret], in backticks or bare",
                  hidden[0] == "- row: `Name: ORDERS_SERVICE_SECRET`, `Value: [secret]`"
                  and hidden[1] == "- row: Name ORDERS_API_KEY | Value [secret]", str(hidden))
    harness.check("a line naming no credential keeps its value", hidden[2] == lines[2], str(hidden))


def test_the_keys_a_read_record_carries_beside_its_notes_are_the_ones_stripped_before_it_is_stored():
    paths = fx.make_paths()
    _write_frames(paths)
    frame_records_service.save_frame_record(paths, ITEMS[0], {"facts": ["A fact."]}, ["frame-000"])
    record = frame_records_service.read_frame_records(paths)[0]
    harness.check("every key the read adds is a RECORD_CONTEXT_KEYS key",
                  set(record) - {"facts"} == set(frame_records_service.RECORD_CONTEXT_KEYS), str(sorted(record)))


def test_the_screen_a_repeat_is_handed_has_its_secrets_hidden():
    paths = fx.make_paths()
    secret = "Qx7~aB9.cD_ef1GhIjK2lMnO3pQrS4tUvW5xYz"
    items = [{**ITEMS[0], "record": {"screen": f'- field "Value" = "{secret}": middle'}}, {**ITEMS[1], "repeatOf": "frame-000"}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    screen = frame_records_service.find_repeated_screen(paths, "frame-001")
    harness.check("the earlier frame's screen comes back with [secret]", secret not in screen and "[secret]" in screen, screen)


def test_a_repeat_of_a_build_step_is_handed_the_build_up_to_that_step():
    """A repeat of a build's second step was handed that step's outline alone, which holds only what the step added,
    and its notes then said the screen gained those parts there."""

    paths = fx.make_paths()
    items = [{**ITEMS[0], "record": {"screen": '- slide\n  - title "Refunds"'}},
             {**ITEMS[1], "stepOf": "frame-000", "record": {"screen": '- step 2 of the screen of frame 0: a note added\n  - text "by card"'}},
             {**ITEMS[2], "repeatOf": "frame-001"}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    screen = frame_records_service.find_repeated_screen(paths, "frame-002")
    harness.check("the first step's outline, then the step's", screen.index('"Refunds"') < screen.index('"by card"'), screen)


def test_a_repeat_of_a_step_outlined_whole_is_handed_the_slide_once_defect():
    # Defect: a build step outlined the whole slide; a repeat of it was handed every step's outline, the slide three times.
    # Fix: an earlier step's outline a later step's outline holds line for line is left out.
    paths = fx.make_paths()
    items = [{**ITEMS[0], "record": {"screen": '- slide\n  - title "Refunds"'}},
             {**ITEMS[1], "stepOf": "frame-000", "record": {"screen": '- a step of the screen of frame 0: the parts below\n'
                                                                       '  - slide\n    - title "Refunds"\n    - text "by card"'}},
             {**ITEMS[2], "repeatOf": "frame-001"}]
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["frames_preparing"], {"items": items})
    screen = frame_records_service.find_repeated_screen(paths, "frame-002")
    harness.check("the title stands once, with the step's text", screen.count('"Refunds"') == 1 and '"by card"' in screen, screen)


def test_an_empty_phase_output_is_refused_as_missing():

    paths = fx.make_paths()
    empty = paths.output_root / output_contract.SUMMARY_FILE
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_text("", encoding="utf-8")
    try:
        phase_file_service.require_phase_output(empty, output_contract.PHASE_NUMBERS["finalize"])
        refused = False
    except MissingPhaseInput:
        refused = True
    harness.check("zero bytes is what a killed write leaves: refused as missing", refused)
