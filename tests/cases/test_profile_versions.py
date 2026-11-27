"""A finished audio run's document written again in another profile inside the same run:
the document it has is set aside whole, phase 4 runs again from the same notes, and every document stays listed."""

import argparse
import io
import json
import tempfile
import zipfile
from pathlib import Path

from support import fixtures as fx
from support import harness

from models import output_contract
from services import document_folder_service
from services import ingesting_profile_service
from services import phase_file_service
from services import profile_version_service
from services import run_artifact_service
from services import speech_file_service

SEGMENTS = [{"start": 0.0, "end": 4.0, "text": "we move the Gateway release to Friday"},
            {"start": 4.0, "end": 8.0, "text": "Jonas writes the notes for AcmeSync"}]
SPEECH = """# Speech

Profile: conversation

## S0001 · Speaker A
We move the Gateway release to Friday.

## S0002 · Speaker B
Jonas writes the notes for AcmeSync.
"""


def _make_finished_run(medium: str = output_contract.AUDIO_MEDIUM,
                       document: str = "# Gateway release\n\nThe release moves to Friday.\n"):
    paths = fx.make_paths(Path(tempfile.mkdtemp()), module="Gateway-call.m4a")
    phase_file_service.write_phase_file(paths, output_contract.PHASE_NUMBERS["transcribe"], {"segments": SEGMENTS})
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": output_contract.LOOP_ITEMS_SCHEMA_VERSION,
                                                   "medium": medium, "items": []})
    speech_file_service.build_speech_path(paths).write_text(SPEECH, encoding="utf-8")
    (paths.output_root / output_contract.NOTES_FILE).write_text("# Notes\n", encoding="utf-8")
    ingesting_profile_service.write_profile(paths.output_root, "conversation", "tag")
    _write_document(paths, document)
    calls = paths.output_root / output_contract.CALLS_DIR_NAME
    calls.mkdir(exist_ok=True)
    (calls / "chapter-01.json").write_text("{}", encoding="utf-8")
    (calls / f"{output_contract.SPEECH_SLUG}.json").write_text("{}", encoding="utf-8")

    return paths


def _write_document(paths, text: str) -> Path:
    paths.summary.write_text(text, encoding="utf-8")
    published = document_folder_service.build_document_path(paths)
    published.parent.mkdir(parents=True, exist_ok=True)
    published.write_text(text, encoding="utf-8")

    return published


def test_a_finished_audio_run_offers_the_profiles_that_read_its_notes_as_they_are():
    paths = _make_finished_run()
    offered = profile_version_service.list_appendable_profiles(paths.output_root)
    harness.check("the profiles that prepare nothing on two paragraphs, never its own nor the one-call one",
                  offered == ["knowledge", "presentation", "meeting"], str(offered))
    pdf = _make_finished_run(medium=output_contract.PDF_TEXT_ONLY_MEDIUM)
    harness.check("a PDF read as text is no audio run", profile_version_service.list_appendable_profiles(pdf.output_root) == [])


def test_the_document_is_set_aside_whole_and_phase_4_left_to_run_again():
    paths = _make_finished_run()
    root = paths.output_root
    old_published = document_folder_service.build_document_path(paths)
    version = profile_version_service.set_document_aside(root, "meeting")
    harness.check("the version holds phase 4's document, its published folder and its call log",
                  (version / output_contract.SUMMARY_FILE).is_file()
                  and (version / old_published.relative_to(root)).is_file()
                  and (version / output_contract.CALLS_DIR_NAME / "chapter-01.json").is_file(), str(list(version.rglob("*"))))
    harness.check("the run keeps phases 1 to 3 and phase 2's call log, and has no document",
                  not paths.summary.exists() and not old_published.exists()
                  and (root / output_contract.SPEECH_FILE).is_file()
                  and (root / output_contract.CALLS_DIR_NAME / f"{output_contract.SPEECH_SLUG}.json").is_file())
    harness.check("the new profile is the run's", ingesting_profile_service.read_profile(root) == {
        "profile": "meeting", "chosenBy": "convert"})
    try:
        profile_version_service.set_document_aside(root, "sprint-planning")
        harness.check("a profile whose phase 3 differs is refused", False)
    except ValueError as refusal:
        harness.check("a run with no document is refused by name", "cannot write its document again" in str(refusal),
                      str(refusal))


def test_every_document_the_run_wrote_is_listed_and_downloaded():
    paths = _make_finished_run()
    root = paths.output_root
    profile_version_service.set_document_aside(root, "meeting")
    harness.check("while phase 4 writes again, the set-aside document is listed",
                  [version["label"] for version in profile_version_service.list_document_versions(root, None)]
                  == ["conversation"])
    published = _write_document(paths, "# Gateway release\n\n> **Decision:** the release moves to Friday.\n")
    versions = profile_version_service.list_document_versions(root, published)
    harness.check("each document by its profile, numbered, the current one last",
                  [(version["number"], version["label"]) for version in versions] == [(1, "conversation"), (2, "meeting")],
                  str(versions))
    data, name = run_artifact_service.build_versions_download(root, 1)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        texts = [archive.read(entry).decode("utf-8") for entry in archive.namelist()]
    harness.check("version 1 downloads the first document", texts == ["# Gateway release\n\nThe release moves to Friday.\n"]
                  and name.endswith("-1-conversation.zip"), f"{name} {texts}")
    record = json.loads((root / profile_version_service.VERSIONS_DIR / "1" / "version.json").read_text(encoding="utf-8"))
    harness.check("the version names its profile", record["profile"] == "conversation", str(record))


def test_the_command_writes_in_place_with_its_flag():
    from cli import convert_command

    parser = argparse.ArgumentParser()
    convert_command.add_convert_arguments(parser)
    args = parser.parse_args(["--output", "/tmp/run", "--to", "meeting", "--in-place"])
    harness.check("--in-place is read", args.is_in_place and args.ingesting_profile == "meeting")
