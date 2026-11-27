"""Phase 4's agenda and chapter headings taken from a PDF's own headings, with no model call,
on the made wiki export (tests/resources/pdf/out/01-confluence-export.pdf, every word invented): phase 1 reads it, and
the agenda and the draft headings are built from what it read. A 1,493-page wiki export's first two steps were each
handed every paragraph, three times the model's window, and blocked before any call."""

import logging
import tempfile
import types
from pathlib import Path

from support import fixtures as fx
from support import harness

from graph.nodes import write_document_node
from models import output_contract
from services import document_file_service
from services import pdf_source_service
from services import phase_file_service
from utils import document_plan_utils

PDF = Path(__file__).resolve().parents[1] / "resources" / "pdf" / "out" / "01-confluence-export.pdf"


def test_a_wiki_export_s_agenda_and_headings_come_from_its_own_headings():
    paths = fx.make_paths(Path(tempfile.mkdtemp()), module=PDF.name)
    transcript, _removed = pdf_source_service.build_page_transcript(PDF, is_read_as_pages=False)
    phase_file_service.write_phase_file(paths, 1, transcript)
    phase_file_service.write_phase_file(paths, 2, {"schemaVersion": 1, "medium": output_contract.PDF_TEXT_ONLY_MEDIUM,
                                                   "items": []})
    ctx = types.SimpleNamespace(paths=paths, log=logging.getLogger("test"))
    harness.check("the made wiki export is planned by its headings", write_document_node._is_planned_by_document_headings(ctx))

    # the export opens with its title alone at the top level, so its chapters are the sections one level down
    first_lines = [segment["text"].splitlines()[0] for segment in transcript["segments"] if segment["text"].startswith("#")]
    levels = [len(line) - len(line.lstrip("#")) for line in first_lines]
    section_titles = [" ".join(line.lstrip("#").replace("*", "").split()) for line, level in zip(first_lines, levels)
                      if level == min(levels) + 1]
    moments = [{"number": number, "start": segment["start"]} for number, segment in enumerate(transcript["segments"], start=1)]
    parts, _announced, title = write_document_node._build_parts_from_headings(ctx, moments)
    harness.check("the lone title is the working title, and each section under it a chapter, in order",
                  levels.count(min(levels)) == 1 and title == " ".join(first_lines[0].lstrip("#").replace("*", "").split())
                  and [part["title"] for part in parts] == section_titles, str((parts, section_titles)))
    harness.check("the agenda is written where its readers find it",
                  len(document_file_service.read_parts(paths).get("points") or []) == len(parts))

    entries = [{"id": f"S{number:04d}", "timestamps": [segment["start"]]}
               for number, segment in enumerate(transcript["segments"], start=1)]
    drafts = write_document_node._build_draft_headings_from_document(ctx, entries, parts)
    starts = {entry["id"] for entry in entries}
    harness.check("each draft heading starts at a paragraph of its own chapter",
                  all(heading["first_entry_id"] in starts for draft in drafts for heading in draft["headings"]), str(drafts))
    groups = document_plan_utils.split_by_parts(entries, [part["start"] for part in parts])
    harness.check("every paragraph falls in one chapter", sum(len(group) for group in groups) == len(entries),
                  str([len(group) for group in groups]))
