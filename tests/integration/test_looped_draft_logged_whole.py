"""A reply that writes a chapter's draft over and over (DEFECTS 220), on a made-up draft shaped like the one a public
froscon 2024 talk's chapter wrote as text before it was stopped as a loop after 795 s: two headings, ids in brackets,
a cut placed. Run through the real conversation loop of phase 5 and the real stream's loop check, the log must keep
the reply whole so the loop can be checked, and the conversation only its first characters."""

import json
import tempfile
from pathlib import Path

from langchain_core.messages import AIMessage

from support import fakes
from support import harness
from cases import test_review_phase as review_cases

DRAFT = ("### Board overview and host integration (T<chapter>.1)\n"
         "The board's controller is an AcmeChip 300 with 32 KB of flash and 2 KB of RAM [F5.1]. A 4 MHz crystal and "
         "four analog inputs sit beside it [F5.2].\n\n"
         "![The assembled board](cut-001 \"The board seen from above\")\n\n"
         "### Inputs, outputs and the radio link (T<chapter>.2)\n"
         "Relays switch the outputs and optocouplers guard the inputs [F9.1]. A 433 MHz radio joins the boards in one "
         "network [F9.2], [S0140].\n\n")


def test_a_chapter_draft_written_over_and_over_is_logged_whole_defect():
    # The log kept the first 2,000 characters of a chapter stopped as a loop, so nobody could check the loop
    paths = review_cases._build_finished_run(Path(tempfile.mkdtemp()))
    reply = DRAFT * 40
    model = fakes.scripted(AIMessage(content=reply), review_cases._submit("submit_review", todos=[]))
    review_cases._run_phase_5(review_cases._load_run_context(paths, model))
    rows = [json.loads(line) for log in Path(paths.repository).rglob("conversation.jsonl")
            for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    said = [row["said"] for row in rows if DRAFT[:40] in (row.get("said") or "")]
    harness.check("the looped draft was stopped and logged whole", bool(said) and len(said[0]) == len(reply),
                  str([len(text) for text in said]))
    kept = [str(message.content) for message in (model.calls[1] if len(model.calls) > 1 else [])
            if isinstance(message, AIMessage) and DRAFT[:40] in str(message.content)]
    harness.check("and the next call is sent its first characters only", bool(kept) and len(kept[0]) <= 2_000,
                  str([len(text) for text in kept]))
