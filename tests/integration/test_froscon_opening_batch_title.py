"""froscon 2024 talk 3147's first batch, its title card and title slide, was titled "Renaissance of Terminal User
Interfaces with Rust presentation" and refused as naming the meeting (DEFECTS 309). The first batch's closing now names
the shape of an opening's title; through the real title check under the talk's real recording name, that shape passes
and the refused title is still refused."""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from support import fixtures as fx
from support import harness

from models.validation_report import ValidationReport
from prompts import frames_processing_messages
from validators import item_validator

RECORDING = "froscon2024-3147-eng-Renaissance_of_Terminal_User_Interfaces_with_Rust_sd.mp4"


def test_the_opening_title_the_closing_names_passes_the_talk_s_title_check_defect():
    # The refused title and the shape the closing now asks for, judged by the real check for this recording
    paths = dataclasses.replace(fx.make_paths(), module=Path(RECORDING))
    refused = ValidationReport(subject="frames-000-001")
    item_validator._check_batch_title(refused, paths, "frames-000-001",
                                      "Renaissance of Terminal User Interfaces with Rust presentation")
    harness.check("the talk's own title is refused", "BATCH_TITLE_GENERIC" in harness.codes(refused),
                  str([finding.code for finding in refused.findings]))
    named = re.search(r'opening of title slides is "([^"]+)"', frames_processing_messages.FIRST_BATCH_TITLES)
    harness.check("the first batch's closing names an opening's title", named is not None,
                  frames_processing_messages.FIRST_BATCH_TITLES)
    if named is None:
        return
    shaped = ValidationReport(subject="frames-000-001")
    item_validator._check_batch_title(shaped, paths, "frames-000-001", named.group(1))
    harness.check("the opening's title it names passes", not shaped.findings,
                  str([finding.detail for finding in shaped.findings]))
