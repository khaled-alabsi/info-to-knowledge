"""A Fast (direct-draft) video run names its phases 3 and 4 "processing and draft writing" and "document reshaping":
no frame is processed on its own and no document is written from notes, the draft is written in phase 3 and reshaped
into the document in phase 4. Every other workflow keeps "frames processing" and "document writing". The names reach
the phase list the page asks for, the run lists, the instructions catalogue, the header of the run's console and its
closing table.
"""

import tempfile
from pathlib import Path

from support import harness

from cli import run_command
from models import output_contract
from models import video_workflow_vocabulary
from models.run_start_checks_result import RunStartChecksResult
from models.web_api_request import PhaseFlowRequest
from services import instruction_catalogue_service
from services import performance_log_service
from services import run_status_service
from services import video_workflow_service
from services import web_api_service

_FAST_THREE = "processing and draft writing"
_FAST_FOUR = "document reshaping"


def _titles(workflow: str, **flow: str) -> dict[int, str]:
    phases = web_api_service.list_phases(PhaseFlowRequest(medium="video", workflow=workflow, **flow))

    return {phase["number"]: phase["title"] for phase in phases}


def test_a_fast_run_s_phase_list_names_phases_3_and_4_for_what_they_do_defect():
    fast = _titles(video_workflow_vocabulary.DIRECT_DRAFT)
    harness.check("phase 3 is processing and draft writing and phase 4 document reshaping",
                  fast[3] == _FAST_THREE and fast[4] == _FAST_FOUR, str(fast))
    harness.check("the other phases keep their names", fast[2] == "frames preparing" and fast[7] == "final document reshaping",
                  str(fast))
    for workflow in (video_workflow_vocabulary.QUALITY, video_workflow_vocabulary.FAST_PROCESSING):
        other = _titles(workflow)
        harness.check(f"a {workflow} run keeps frames processing and document writing",
                      other[3] == "frames processing" and other[4] == "document writing", str(other))
    harness.check("an audio recording keeps its own names whatever the workflow says",
                  web_api_service.list_phases(PhaseFlowRequest(medium="audio",
                                                               workflow=video_workflow_vocabulary.DIRECT_DRAFT))[2]["title"]
                  == "notes preparing")


def test_a_fast_run_s_phase_help_describes_the_draft_not_the_frame_notes_defect():
    phases = {phase["number"]: phase for phase in web_api_service.list_phases(
        PhaseFlowRequest(medium="video", workflow=video_workflow_vocabulary.DIRECT_DRAFT))}
    harness.check("phase 3 writes the draft", output_contract.DRAFT_FILE in phases[3]["writes"]
                  and "draft" in phases[3]["summary"], str(phases[3]))
    harness.check("phase 4 reads the draft", output_contract.DRAFT_FILE in phases[4]["reads"]
                  and "draft" in phases[4]["summary"], str(phases[4]))


def test_a_run_list_names_each_run_s_phases_by_its_workflow_defect():
    fast = Path(tempfile.mkdtemp())
    video_workflow_service.write_chosen_workflow(fast, video_workflow_vocabulary.DIRECT_DRAFT, "user")
    quality = Path(tempfile.mkdtemp())
    video_workflow_service.write_chosen_workflow(quality, video_workflow_vocabulary.QUALITY, "user")
    fast_titles = run_status_service._list_phase_titles(fast, False, "", "")
    quality_titles = run_status_service._list_phase_titles(quality, False, "", "")
    harness.check("a fast run's phases 3 and 4", (fast_titles["3"], fast_titles["4"]) == (_FAST_THREE, _FAST_FOUR),
                  str(fast_titles))
    harness.check("a quality run's phases 3 and 4", (quality_titles["3"], quality_titles["4"])
                  == ("frames processing", "document writing"), str(quality_titles))
    harness.check("an audio run never takes them", run_status_service._list_phase_titles(fast, True, "", "")["3"]
                  == "notes preparing")


def test_the_instructions_page_names_a_direct_draft_variant_s_phases_defect():
    workflows = {workflow["key"]: workflow for workflow in instruction_catalogue_service.build_catalogue()["workflows"]}
    phases = {phase["number"]: phase for phase in workflows["video"]["phases"]}
    harness.check("phases 3 and 4 carry the direct draft's titles beside the video workflow's",
                  phases[3]["variant_titles"] == {video_workflow_vocabulary.DIRECT_DRAFT: _FAST_THREE}
                  and phases[4]["variant_titles"] == {video_workflow_vocabulary.DIRECT_DRAFT: _FAST_FOUR}
                  and phases[3]["title"] == "frames processing" and phases[4]["title"] == "document writing",
                  str({number: (phase["title"], phase["variant_titles"]) for number, phase in phases.items()}))


def test_a_fast_run_s_console_header_and_closing_table_use_the_same_names_defect():
    checks = RunStartChecksResult(transcription="audio: mlx", diarization="diarization: off")
    fast = run_command.render_model_lines(checks, {}, is_audio=False, has_no_critic=True, is_direct_draft=True)
    quality = run_command.render_model_lines(checks, {}, is_audio=False)
    harness.check("the header's phase 3 and 4 lines carry the fast names",
                  fast[2].split()[0] == "processing_and_draft_writing" and fast[3].split()[0] == "document_reshaping",
                  "\n".join(fast))
    harness.check("the label column is as wide as the longest label, so every line lines up",
                  len({len(line) - len(line[2:].split(maxsplit=1)[1]) for line in fast[:6]}) == 1, "\n".join(fast))
    harness.check("a run that is not a fast one keeps frames_processing and finalize",
                  quality[2].split()[0] == "frames_processing" and quality[3].split()[0] == "finalize", "\n".join(quality))
    phases = [{"phase": "frames_processing", "number": 3, "status": "complete", "durationS": 60.0},
              {"phase": "finalize", "number": 4, "status": "complete", "durationS": 30.0}]
    video = {"status": "complete", "durationS": 90.0, "videoS": 900.0, "realTimeFactor": 0.1}
    fast_table = performance_log_service.render_performance_table(phases, video, is_direct_draft=True).splitlines()
    plain_table = performance_log_service.render_performance_table(phases, video).splitlines()
    harness.check("the closing table names the fast run's phases by their fast names",
                  [line.split()[0] for line in fast_table[1:3]] == ["processing_and_draft_writing", "document_reshaping"],
                  "\n".join(fast_table))
    harness.check("any other run's table is unchanged",
                  [line.split()[0] for line in plain_table[1:3]] == ["frames_processing", "finalize"], "\n".join(plain_table))
