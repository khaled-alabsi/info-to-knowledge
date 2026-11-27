"""Choosing the configured model of each phase: `phaseModels`, `run --phase-model`, the
job's run line and what `models` prints. Every model name is invented."""

from pathlib import Path

from support import harness

import config
from cli import run_command
from models.run_errors import BadTarget, ModelUnavailable
from models.run_job import RunJobRequest
from services import chat_model_service, job_queue_service, run_operations_service

SMALL = config.ModelSettings(spec="lmstudio:acme-small-vl")


def _with_small_model(check):
    saved_models, saved_phases = dict(config.settings.models), dict(config.settings.phase_models)
    config.settings.models["acme-small"] = SMALL
    try:
        check()
    finally:
        config.settings.models.clear()
        config.settings.models.update(saved_models)
        config.settings.phase_models = saved_phases
        chat_model_service.get_model.cache_clear()


def test_the_flag_is_read_into_phase_models_and_a_wrong_pair_is_refused_by_name():
    def check():
        harness.check("a phase and a configured model", run_command.read_phase_models(
            ["frames_processing=acme-small", "review = acme-small"])
                      == {"frames_processing": "acme-small", "review": "acme-small"})
        for pair, named in (("transcribe=acme-small", "transcribe"), ("finalize=missing", "missing"),
                            ("finalize", "PHASE=NAME")):
            try:
                run_command.read_phase_models([pair])
                harness.check(f"{pair} is refused", False)
            except BadTarget as refused:
                harness.check(f"{pair} is refused, naming {named}", named in str(refused), str(refused))

    _with_small_model(check)


def test_the_parser_takes_the_flag_more_than_once():
    args = run_command_parser().parse_args(["run", "--video", "Jonas-call.mp4", "--output", "out",
                                            "--phase-model", "frames_processing=acme-small",
                                            "--phase-model", "critique_collection=acme-small"])
    harness.check("both pairs are kept, in order",
                  args.phase_models == ["frames_processing=acme-small", "critique_collection=acme-small"],
                  str(args.phase_models))


def run_command_parser():
    from cli import build_parser

    return build_parser()


def test_the_job_carries_its_choice_onto_its_run_line_and_through_the_queue_file():
    request = RunJobRequest(video=Path("/inbox/Jonas-call.mp4"), output_root=Path("/output/Jonas-call"),
                            phase_models=("frames_processing=acme-small",))
    command = job_queue_service.build_run_command(request)
    harness.check("the run line names the phase's model",
                  command[command.index("--phase-model") + 1] == "frames_processing=acme-small", str(command))
    harness.check("the queue file keeps it", RunJobRequest.from_dict(request.as_dict()) == request)
    harness.check("a job with no choice has no flag",
                  "--phase-model" not in job_queue_service.build_run_command(
                      RunJobRequest(video=Path("/a.mp4"), output_root=Path("/o"))))


def test_a_phase_given_another_model_is_built_and_printed_with_it():
    def check():
        config.settings.phase_models = {"frames_processing": "acme-small"}
        chat_model_service.get_model.cache_clear()
        rows = {row["phase"]: row for row in chat_model_service.describe_phase_models()}
        harness.check("phase 3 resolves to the chosen model", (rows["frames_processing"]["name"],
                                                               rows["frames_processing"]["model"])
                      == ("acme-small", "acme-small-vl"), str(rows["frames_processing"]))
        harness.check("phase 4 keeps its default", rows["finalize"]["name"] == "document", str(rows["finalize"]))
        lines = run_operations_service.describe_models().lines
        harness.check("`models` prints the phase with the model's name and spec",
                      any(line.split()[:3] == ["frames_processing", "acme-small", "lmstudio:acme-small-vl"]
                          for line in lines), "\n".join(lines))
        harness.check("and lists every configured model", "acme-small (lmstudio:acme-small-vl)" in lines[-1], lines[-1])
        try:
            chat_model_service.get_model("transcribe")
            harness.check("a phase that calls no model is refused", False)
        except ModelUnavailable as refused:
            harness.check("a phase that calls no model is refused by name", "transcribe" in str(refused), str(refused))

    _with_small_model(check)


def test_a_phase_named_for_a_model_that_is_not_configured_is_refused_by_name_everywhere():
    def check():
        config.settings.phase_models = {"frames_processing": "no-such-model"}
        for label, action in (("the structured method", lambda: chat_model_service.resolve_structured_method("frames_processing")),
                              ("the host probe", chat_model_service.probe_hosts)):
            try:
                action()
                outcome = "no refusal"
            except ModelUnavailable as refused:
                outcome = f"named: {refused}"
            except ValueError as raw:
                outcome = f"raw ValueError: {raw}"
            harness.check(f"{label} refuses it as an unavailable model, naming the phase",
                          outcome.startswith("named:") and "frames_processing" in outcome, outcome)

    _with_small_model(check)
