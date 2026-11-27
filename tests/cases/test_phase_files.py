"""Numbered phase files: a phase reads the file the phase before it wrote, and refuses by name when
that file is not there. Reading nothing and carrying on is the failure these cases exist to stop."""

from support import fixtures as fx
from support import harness

from models import output_contract
from models.pipeline_errors import MissingPhaseInput
from services import phase_file_service

FIRST = min(output_contract.PHASE_FILES)


def test_a_written_phase_file_reads_back():
    paths = fx.make_paths()
    written = phase_file_service.write_phase_file(paths, FIRST, {"items": [{"slug": "a"}]})
    harness.check("the file lands under the output root", written.parent == paths.output_root, str(written))
    harness.check("under the name the contract gives it",
                  written.name == output_contract.PHASE_FILES[FIRST]["file"], written.name)
    harness.check("and reads back as written",
                  phase_file_service.read_phase_file(paths, FIRST) == {"items": [{"slug": "a"}]})


def test_a_missing_input_is_refused_naming_the_file_and_its_producer():
    paths = fx.make_paths()
    try:
        phase_file_service.read_phase_file(paths, FIRST)
    except MissingPhaseInput as exc:
        row = output_contract.PHASE_FILES[FIRST]
        harness.check("the refusal names the missing file", row["file"] in str(exc), str(exc))
        harness.check("and the phase that produces it", f"phase {FIRST}" in str(exc), str(exc))
    else:
        harness.check("a missing phase file raises", False)


def test_an_empty_file_is_refused_like_a_missing_one():
    """Zero bytes is what a killed write leaves, and it must not read as an empty answer."""

    paths = fx.make_paths()
    phase_file_service.build_phase_file_path(paths, FIRST).write_text("", encoding="utf-8")
    try:
        phase_file_service.read_phase_file(paths, FIRST)
    except MissingPhaseInput:
        harness.check("an empty phase file is refused", True)
    else:
        harness.check("an empty phase file is refused", False)


def test_a_phase_number_the_contract_does_not_declare_raises():
    paths = fx.make_paths()
    try:
        phase_file_service.build_phase_file_path(paths, 99)
    except KeyError as exc:
        harness.check("the refusal names the number", "99" in str(exc), str(exc))
    else:
        harness.check("an undeclared phase number raises", False)


def test_every_declared_phase_names_its_file_and_its_producer():
    for number, row in output_contract.PHASE_FILES.items():
        harness.check(f"phase {number} names a file", bool(row.get("file")), str(row))
        harness.check(f"phase {number} names the node phase that writes it", bool(row.get("phase")), str(row))
