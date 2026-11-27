"""The settings files: `playbook.default.json`, the default settings, kept in git, read first; `playbook.local.json`, this machine's,
read on top of it; and `playbook.<name>.json` when a run line says `--profile <name>`, read on top of both. The owner
(2026-10-02): a machine without the local file runs on the default settings, not on the code's bare ones."""

from __future__ import annotations

import json
import logging
import os
import time
import tempfile
from pathlib import Path

from support import harness

import config
from cli import run_command
from services import host_model_service
from services import run_status_service
from services import settings_service


def test_the_local_file_is_read_on_top_of_the_default_file():
    folder = Path(tempfile.mkdtemp())
    default, local = folder / "playbook.default.json", folder / "playbook.local.json"
    default.write_text(json.dumps({"_comment": ["notes"], "framesProcessing": {"framesPerCall": 28, "repairCalls": 0},
                                   "frames": {"mode": "change"}}), encoding="utf-8")
    harness.check("with no local file the default file's values apply",
                  config.read_settings_files(default, local) == {"framesProcessing": {"framesPerCall": 28, "repairCalls": 0},
                                                                 "frames": {"mode": "change"}})
    local.write_text(json.dumps({"framesProcessing": {"framesPerCall": 6}}), encoding="utf-8")
    harness.check("a local value replaces the default's, and the default's other values stay",
                  config.read_settings_files(default, local) == {"framesProcessing": {"framesPerCall": 6, "repairCalls": 0},
                                                                 "frames": {"mode": "change"}})
    harness.check("with neither file the code's own defaults apply",
                  config.read_settings_files(folder / "none.json", folder / "none-local.json") == {})


def test_the_shipped_default_file_holds_placeholders_and_no_host_of_a_person():
    """The shipped default file is the public one: every key a person may change, with a value that works on any machine
    (`localhost` hosts, no worker set); a person's own hosts and worker sets live in `playbook.local.json`, read on top."""

    text = config.DEFAULT_CONFIG_FILE.read_text(encoding="utf-8")
    shipped = json.loads(text)
    harness.check("the default file is there and is a settings object", isinstance(shipped, dict) and "framesProcessing" in shipped)
    harness.check("it names no home-network host", "192.168." not in text and "http://pc." not in text)
    harness.check("it holds no worker set of its own", [name for name in shipped["workerSets"] if not name.startswith("_")] == [])


def test_a_profile_is_read_on_top_of_the_local_file():
    """The owner asked for the profile a run line names to win over this machine's own file: the tolerated values live
    in the default settings, and `--profile strict` must not be undone by the local file it is read beside."""

    folder = Path(tempfile.mkdtemp())
    default, local = folder / "playbook.default.json", folder / "playbook.local.json"
    profile = folder / "playbook.strict.json"
    default.write_text(json.dumps({"framesProcessing": {"framesPerCall": 7, "repairCalls": 3},
                                   "frames": {"mode": "change"}}), encoding="utf-8")
    local.write_text(json.dumps({"framesProcessing": {"framesPerCall": 7, "repairCalls": 3}}), encoding="utf-8")
    profile.write_text(json.dumps({"_comment": "notes", "framesProcessing": {"_comment": "n", "repairCalls": 0}}),
                       encoding="utf-8")
    harness.check("the profile's value wins over the local file's, and every other value stays",
                  config.read_settings_files(default, local, profile) ==
                  {"framesProcessing": {"framesPerCall": 7, "repairCalls": 0}, "frames": {"mode": "change"}})
    harness.check("a note beside the settings it explains is dropped at every depth, never read as a setting",
                  "_comment" not in config.read_settings_files(default, local, profile)["framesProcessing"])


def test_the_strict_profile_turns_every_repair_off():
    """The shipped profile, in git, used only when asked for. Zero repair of a frame, and a refused frame tool stops
    the run, while the default settings hold the tolerated values."""

    name = config.PROFILE_NAME
    overrides = config.read_settings_files(config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE,
                                           config.build_profile_path("strict"))
    strict = config.Settings(**overrides)
    harness.check("strict blocks a refused frame at once and stops on a refused frame tool",
                  (strict.frames_processing.repair_calls == 0 and strict.frames_processing.single_frame_attempts == 0
                   and strict.budgets.fail_on_frame_cut_fail),
                  f"{strict.frames_processing.repair_calls}, {strict.frames_processing.single_frame_attempts}, "
                  f"{strict.budgets.fail_on_frame_cut_fail}")
    harness.check("the default settings let a batch repair and a refused frame tool cost only its turn",
                  (config.settings.frames_processing.repair_calls > 0
                   and config.settings.frames_processing.single_frame_attempts > 0
                   and not config.settings.budgets.fail_on_frame_cut_fail),
                  f"{config.settings.frames_processing.repair_calls}, "
                  f"{config.settings.frames_processing.single_frame_attempts}, "
                  f"{config.settings.budgets.fail_on_frame_cut_fail}")
    harness.check("a profile with no file is refused by name, never run on the settings it was to replace",
                  _refusal_for("no-such-profile").startswith("no profile 'no-such-profile'"),
                  _refusal_for("no-such-profile"))
    harness.check("loading a profile leaves the one in force unchanged when it fails", config.PROFILE_NAME == name)


def _refusal_for(name: str) -> str:
    try:
        config.load_profile(name)
    except (FileNotFoundError, ValueError) as exc:
        return str(exc)

    return "it was accepted"


def test_the_run_log_names_every_repair_setting_with_its_value():
    """The owner asked to see in a run's log how much repair it had, not only which files it read."""

    frames, budgets = config.settings.frames_processing, config.settings.budgets
    previous = (frames.repair_calls, frames.single_frame_attempts, frames.is_critique_repair_on, budgets.max_attempts,
                budgets.ask_why_after_rejection, budgets.chapter_dry_runs, budgets.write_chapter_attempts,
                budgets.fail_on_frame_cut_fail)
    frames.repair_calls, frames.single_frame_attempts, frames.is_critique_repair_on = 2, 0, False
    budgets.max_attempts, budgets.ask_why_after_rejection, budgets.chapter_dry_runs = 4, True, False
    budgets.write_chapter_attempts, budgets.fail_on_frame_cut_fail = 5, True
    try:
        line = run_command._describe_repair_settings()
    finally:
        (frames.repair_calls, frames.single_frame_attempts, frames.is_critique_repair_on, budgets.max_attempts,
         budgets.ask_why_after_rejection, budgets.chapter_dry_runs, budgets.write_chapter_attempts,
         budgets.fail_on_frame_cut_fail) = previous
    harness.check("each setting by its playbook name, with the value in force",
                  line == "framesProcessing.repairCalls=2 singleFrameAttempts=0 isCritiqueRepairOn=off · "
                          "budgets.maxAttempts=4 askWhyAfterRejection=on chapterDryRuns=off writeChapterAttempts=5 "
                          "failOnFrameCutFail=on", line)


def _answer_no_host(provider: str,
                    base_url: str) -> None:
    """A host lister for the suite, which reaches no model host: none answers."""

    return None


def _swap_settings_files(folder: Path) -> tuple:
    previous = config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT
    config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE = folder / "playbook.default.json", folder / "playbook.local.json"
    config.PLAYBOOK_ROOT = folder

    return previous


def test_the_settings_list_names_each_value_its_file_and_its_comment():
    """The page shows every setting by its playbook name, where its value comes from, and the
    comment written above it in config.py."""

    folder = Path(tempfile.mkdtemp())
    (folder / "playbook.default.json").write_text(json.dumps({"budgets": {"maxAttempts": 3}}), encoding="utf-8")
    (folder / "playbook.local.json").write_text(json.dumps({"budgets": {"cutsPerFrame": 4}}), encoding="utf-8")
    (folder / "playbook.strict.json").write_text(json.dumps({"budgets": {"maxAttempts": 1}}), encoding="utf-8")
    previous = _swap_settings_files(folder)
    settings_service.set_host_lister(_answer_no_host)
    try:
        rows = {row["key"]: row for row in settings_service.list_settings()}
        strict = {row["key"]: row for row in settings_service.list_settings("strict")}
    finally:
        config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT = previous
        settings_service.set_host_lister(host_model_service.list_host_models)
    harness.check("a setting whose type names its values carries them, the page offers them as a list",
                  rows["frames.boardHandler"]["choices"] == ["image-diff-only", "text-diff-integration", "text-only-integration"]
                  and rows["budgets.maxAttempts"]["choices"] == [], str(rows["frames.boardHandler"]))
    harness.check("a value from the default file, one from the local file, one from the code",
                  (rows["budgets.maxAttempts"]["value"], rows["budgets.maxAttempts"]["source"]) == (3, "default")
                  and rows["budgets.cutsPerFrame"]["source"] == "local" and rows["review.isOn"]["source"] == "code",
                  str([rows["budgets.maxAttempts"], rows["budgets.cutsPerFrame"]]))
    harness.check("a profile's value wins and is named as its source",
                  (strict["budgets.maxAttempts"]["value"], strict["budgets.maxAttempts"]["source"]) == (1, "strict"))
    harness.check("each row carries its kind and the comment above its field",
                  rows["review.isOn"]["kind"] == "bool" and "phase 5" in rows["review.isOn"]["comment"],
                  str(rows["review.isOn"]))


def test_a_setting_is_saved_in_the_owners_file_alone_and_a_value_set_back_to_the_default_leaves_it_defect():
    # Every save wrote both the shipped default file and the owner's own, so the owner's file became a second full copy
    # (141 of 189 values the same) and settings "made no sense". A save now writes the owner's file alone, and only what differs
    folder = Path(tempfile.mkdtemp())
    default = {"_comment": ["the default settings"], "budgets": {"_attempts": "a note", "maxAttempts": 3}}
    (folder / "playbook.default.json").write_text(json.dumps(default, indent=2) + "\n", encoding="utf-8")
    (folder / "playbook.local.json").write_text(json.dumps({"_comment": ["mine"]}, indent=2) + "\n", encoding="utf-8")
    (folder / "playbook.strict.json").write_text(json.dumps({"budgets": {"maxAttempts": 1}}), encoding="utf-8")
    previous = _swap_settings_files(folder)
    try:
        saved = settings_service.save_setting("budgets.maxAttempts", 5)
        local_after_save = json.loads((folder / "playbook.local.json").read_text(encoding="utf-8"))
        refused = settings_service.save_setting("budgets.maxAttempts", "many")
        unknown = settings_service.save_setting("budgets.noSuchKey", 1)
        in_profile = settings_service.save_setting("budgets.maxAttempts", 2, "strict")
        back = settings_service.save_setting("budgets.maxAttempts", 3)
    finally:
        config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT = previous
    shipped, local = [json.loads((folder / name).read_text(encoding="utf-8")) for name in ("playbook.default.json", "playbook.local.json")]
    harness.check("saved in the owner's file alone, the shipped file untouched",
                  saved.exit_code == 0 and local_after_save["budgets"]["maxAttempts"] == 5 and shipped == default,
                  str([local_after_save, shipped]))
    harness.check("a value set back to the shipped default leaves the owner's file, its notes kept",
                  back.exit_code == 0 and local == {"_comment": ["mine"]}, str(local))
    harness.check("a value the schema refuses is not written, and the reason is said",
                  refused.exit_code == 2 and "refused" in refused.output and unknown.exit_code == 2, refused.output)
    harness.check("a profile's change goes into its own file only",
                  in_profile.exit_code == 0 and json.loads((folder / "playbook.strict.json").read_text())["budgets"]["maxAttempts"] == 2,
                  in_profile.output)


def test_the_owners_file_is_trimmed_to_the_values_that_differ_from_the_shipped_defaults_defect():
    # The owner's file repeated most of the shipped defaults, so a value could not be told as the owner's own or the
    # default's. The trim takes out every value the same as the shipped one, keeping the notes and the values that differ
    folder = Path(tempfile.mkdtemp())
    default = {"budgets": {"maxAttempts": 3, "cutsPerFrame": 2}, "review": {"isOn": True}}
    local = {"_comment": ["mine"], "budgets": {"maxAttempts": 3, "cutsPerFrame": 4}, "review": {"_note": "x", "isOn": True}}
    (folder / "playbook.default.json").write_text(json.dumps(default), encoding="utf-8")
    (folder / "playbook.local.json").write_text(json.dumps(local), encoding="utf-8")
    previous = _swap_settings_files(folder)
    try:
        result = settings_service.trim_local_settings()
    finally:
        config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT = previous
    trimmed = json.loads((folder / "playbook.local.json").read_text(encoding="utf-8"))
    harness.check("only the differing value and the notes stay",
                  trimmed == {"_comment": ["mine"], "budgets": {"cutsPerFrame": 4}} and result.output.startswith("2 values"),
                  str([trimmed, result.output]))


def test_a_run_is_told_its_settings_changed_only_when_a_file_it_reads_changed_after_it_read_them():
    folder = Path(tempfile.mkdtemp())
    for name in ("playbook.default.json", "playbook.local.json", "playbook.strict.json"):
        (folder / name).write_text("{}", encoding="utf-8")
        os.utime(folder / name, (1000, 1000))
    previous = _swap_settings_files(folder)
    try:
        before = run_status_service.has_newer_settings({"settingsReadAt": 2000, "settingsProfile": ""})
        os.utime(folder / "playbook.strict.json", (3000, 3000))
        other_profile = run_status_service.has_newer_settings({"settingsReadAt": 2000, "settingsProfile": ""})
        own_profile = run_status_service.has_newer_settings({"settingsReadAt": 2000, "settingsProfile": "strict"})
        os.utime(folder / "playbook.local.json", (3000, 3000))
        default_settings = run_status_service.has_newer_settings({"settingsReadAt": 2000, "settingsProfile": ""})
        unknown = run_status_service.has_newer_settings({})
        # A run older than the read time: its process's start stands in, here this test's own process
        os.utime(folder / "playbook.local.json", (time.time() + 60, time.time() + 60))
        started_earlier = run_status_service.has_newer_settings({}, [os.getpid()])
    finally:
        config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT = previous
    harness.check("files older than the read: nothing to say", not before)
    harness.check("a profile this run does not use is not its change", not other_profile)
    harness.check("its own profile changed, or a default settings file: said", own_profile and default_settings)
    harness.check("a run that recorded no read time and has no process says nothing", not unknown)
    harness.check("one with a live process is measured from when that process started", started_earlier)


def test_a_profile_with_a_bad_value_leaves_the_name_and_settings_in_force():
    folder = Path(tempfile.mkdtemp())
    (folder / "playbook.broken.json").write_text(json.dumps({"budgets": {"maxAttempts": "many"}}), encoding="utf-8")
    name, settings, root = config.PROFILE_NAME, config.settings, config.PLAYBOOK_ROOT
    config.PLAYBOOK_ROOT = folder
    try:
        try:
            config.load_profile("broken")
            outcome = "it was accepted"
        except ValueError as exc:
            outcome = type(exc).__name__
    finally:
        config.PLAYBOOK_ROOT = root
    harness.check("the bad value is refused", outcome != "it was accepted", outcome)
    harness.check("and the profile name in force is unchanged", config.PROFILE_NAME == name, config.PROFILE_NAME)
    harness.check("as are its settings", config.settings is settings)


def test_a_settings_file_that_is_not_utf8_adds_nothing():
    path = Path(tempfile.mkdtemp()) / "playbook.local.json"
    path.write_bytes('{"budgets": {"maxAttempts": 2}} é'.encode("latin-1"))
    try:
        outcome = config.read_settings_file(path)
    except UnicodeDecodeError as exc:
        outcome = f"raised {exc}"
    harness.check("an unreadable file adds nothing, as its docstring says", outcome == {}, str(outcome))


def test_a_settings_file_that_is_not_an_object_warns_by_name():
    path = Path(tempfile.mkdtemp()) / "playbook.local.json"
    path.write_text(json.dumps([{"budgets": {"maxAttempts": 2}}]), encoding="utf-8")
    warnings = []
    handler = logging.Handler()
    handler.emit = lambda record: warnings.append(record.getMessage())
    logger = logging.getLogger(config.PLAYBOOK_NAME)
    logger.addHandler(handler)
    try:
        outcome = config.read_settings_file(path)
    finally:
        logger.removeHandler(handler)
    harness.check("a file holding a list adds nothing", outcome == {}, str(outcome))
    harness.check("and says so, naming the file", any("playbook.local.json" in text for text in warnings), str(warnings))


def test_a_key_that_runs_through_a_value_is_refused_by_name():
    folder = Path(tempfile.mkdtemp())
    for name in ("playbook.default.json", "playbook.local.json"):
        (folder / name).write_text(json.dumps({"budgets": {"maxAttempts": 3}}), encoding="utf-8")
    previous = _swap_settings_files(folder)
    try:
        try:
            through = settings_service.save_setting("budgets.maxAttempts.extra", 1)
            answer = (through.exit_code, through.output)
        except Exception as exc:
            answer = (None, f"{type(exc).__name__}: {exc}")
    finally:
        config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT = previous
    harness.check("refused as no setting, the files untouched",
                  answer[0] == 2 and "not a setting" in str(answer[1])
                  and json.loads((folder / "playbook.local.json").read_text()) == {"budgets": {"maxAttempts": 3}},
                  str(answer))


def test_a_note_key_or_a_file_that_is_no_settings_object_is_never_written():
    folder = Path(tempfile.mkdtemp())
    default_settings = {"_comment": ["the default settings"], "budgets": {"maxAttempts": 3}}
    for name in ("playbook.default.json", "playbook.local.json"):
        (folder / name).write_text(json.dumps(default_settings), encoding="utf-8")
    (folder / "playbook.strict.json").write_text("[1, 2]", encoding="utf-8")
    previous = _swap_settings_files(folder)
    try:
        note = settings_service.save_setting("_comment", 1)
        try:
            listed = settings_service.save_setting("budgets.maxAttempts", 2, "strict")
            listed = (listed.exit_code, listed.output)
        except Exception as exc:
            listed = (None, f"{type(exc).__name__}: {exc}")
    finally:
        config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT = previous
    harness.check("a `_` key is refused as a note, the notes kept",
                  note.exit_code == 2 and json.loads((folder / "playbook.local.json").read_text()) == default_settings, note.output)
    harness.check("a file that is no settings object is refused and left as it was",
                  listed[0] == 2 and (folder / "playbook.strict.json").read_text() == "[1, 2]", str(listed))


def test_a_profile_with_no_file_is_refused_by_listing_and_saving_alike():
    folder = Path(tempfile.mkdtemp())
    for name in ("playbook.default.json", "playbook.local.json"):
        (folder / name).write_text("{}", encoding="utf-8")
    previous = _swap_settings_files(folder)
    try:
        saved = settings_service.save_setting("budgets.maxAttempts", 2, "nosuch").output
        try:
            settings_service.list_settings("nosuch")
            listed = "listed"
        except Exception as exc:
            listed = str(exc)
    finally:
        config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT = previous
    harness.check("both refuse it in the same words", listed == saved and "playbook.nosuch.json" in listed,
                  f"{listed!r} / {saved!r}")


def test_a_step_may_not_be_given_no_attempts():
    outcomes = {}
    for attempts in (0, -1):
        try:
            config.Settings(budgets={"maxAttempts": attempts})
            outcomes[attempts] = "accepted"
        except ValueError as exc:
            outcomes[attempts] = type(exc).__name__
    harness.check("0 and -1 attempts are refused, since they block a step before it is tried",
                  all(name != "accepted" for name in outcomes.values()), str(outcomes))


def test_both_default_settings_files_send_four_frames_a_call_defect():
    # At two frames a call the follow-ups took 3.4 times the reading (release test 1), at 8 to 10 2.1 times. The default
    # settings now send four, one step of two from the owner's two. The owner's local file is git-ignored and absent
    # in a fresh clone, so only the default file is checked
    root = Path(__file__).resolve().parents[2]
    settings = json.loads((root / "playbook.default.json").read_text(encoding="utf-8"))
    frames_per_call = settings["framesProcessing"]["framesPerCall"]
    harness.check("the default file holds 4", frames_per_call == 4, str(frames_per_call))


def _list_models_of_made_hosts(provider: str,
                               base_url: str) -> list[str] | None:
    """Two made hosts: this machine's LM Studio serves two models, the other machine's one; Ollama does not answer."""

    hosts = {("lmstudio", "http://localhost:1234/v1"): ["big-model", "small-model"],
             ("openai-compatible", "http://other-mac:1234/v1"): ["big-model"]}

    return hosts.get((provider, base_url))


def _write_two_connection_files(folder: Path) -> None:
    """Default settings with the two shipped hosts and two model entries, one named with a dot, and a profile adding a
    host on another machine and pointing one of the models at it, as a worker set does."""

    hosts = {"lmstudio": {"url": "http://localhost:1234/v1"}, "ollama": {"url": "http://localhost:11434"}}
    models = {"frame": {"spec": "lmstudio:big-model"}, "small.v2": {"spec": "lmstudio:small-model"}}
    for name in ("playbook.default.json", "playbook.local.json"):
        (folder / name).write_text(json.dumps({"hosts": hosts, "models": models}), encoding="utf-8")
    other_values = {"hosts": {"other-mac": {"url": "http://other-mac:1234/v1"}}, "models": {"frame": {"host": "other-mac"}}}
    (folder / "playbook.other.json").write_text(json.dumps(other_values), encoding="utf-8")


def test_every_model_selection_is_offered_as_a_list_with_its_connections():
    """A setting naming a model lists the configured models, a model's spec the models the
    hosts serve, a model's host every entry of `hosts`, and each phase gets its own row."""

    folder = Path(tempfile.mkdtemp())
    _write_two_connection_files(folder)
    previous = _swap_settings_files(folder)
    settings_service.set_host_lister(_list_models_of_made_hosts)
    try:
        rows = {row["key"]: row for row in settings_service.list_settings()}
        other = {row["key"]: row for row in settings_service.list_settings("other")}
    finally:
        config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT = previous
        settings_service.set_host_lister(host_model_service.list_host_models)
    harness.check("a spec row names the host its model runs on, so only that host's entry is ticked",
                  (rows["models.frame.spec"]["chosen_host"], other["models.frame.spec"]["chosen_host"],
                   other["models.frame.spec"]["default_host"])
                  == ("lmstudio", "other-mac", "lmstudio"),
                  str(other["models.frame.spec"]))
    harness.check("each host's model names its host",
                  [option.get("host") for option in other["models.frame.spec"]["choice_options"]]
                  == ["lmstudio", "lmstudio", "other-mac"],
                  str(other["models.frame.spec"]["choice_options"]))
    redundancy = rows["frames.redundancyModel"]
    harness.check("a setting naming a model lists the configured models with their specs, its own value kept, no typing",
                  [(option["value"], option["hint"]) for option in redundancy["choice_options"]]
                  == [("frame", "lmstudio:big-model"), ("small.v2", "lmstudio:small-model"),
                      ("lfm-vl-3b", "set in the files, not on any host listed")] and not redundancy["is_open_choice"],
                  str(redundancy))
    similarity = rows["frames.similarityModel"]
    harness.check("one that may be empty lists the empty value first, naming the value a run uses",
                  similarity["choice_options"][0] == {"value": "", "label": "default: lfm-vl-3b (the redundancy filter's model)",
                                                      "hint": "", "group": ""},
                  str(similarity))
    spec = other["models.frame.spec"]
    harness.check("a spec lists each host's models by their id under the host's heading, one model under each host "
                  "that serves it, and allows typing",
                  [(option["group"], option["label"], option["value"]) for option in spec["choice_options"]]
                  == [("lmstudio at http://localhost:1234/v1", "big-model", "lmstudio:big-model"),
                      ("lmstudio at http://localhost:1234/v1", "small-model", "lmstudio:small-model"),
                      ("other-mac at http://other-mac:1234/v1", "big-model", "openai-compatible:big-model")]
                  and spec["is_open_choice"], str(spec))
    host = other["models.frame.host"]
    harness.check("a model's host lists the empty value and every entry of `hosts`, each with its address",
                  [(option["value"], option["hint"]) for option in host["choice_options"]]
                  == [("", "its default"), ("lmstudio", "http://localhost:1234/v1"),
                      ("ollama", "http://localhost:11434"), ("other-mac", "http://other-mac:1234/v1")]
                  and host["value"] == "other-mac", str(host))
    harness.check("no model row holds an address of its own: the address is its host's",
                  not [key for key in rows if key.startswith("models.") and key.endswith(".baseUrl")])
    phase = rows["phaseModels.frames_processing"]
    harness.check("each phase that calls a model is a row of its own, empty for its default, choosing a configured model",
                  phase["value"] == "" and [option["label"] for option in phase["choice_options"]]
                  == ["default: frame", "frame", "small.v2"] and phase["path"] == ["phaseModels", "frames_processing"],
                  str(phase))
    harness.check("a setting whose type names its values keeps them as plain choices",
                  rows["frames.boardHandler"]["choice_options"] is None and rows["frames.boardHandler"]["choices"],
                  str(rows["frames.boardHandler"]))
    harness.check("a model named with a dot keeps its name whole in the row's path",
                  rows["models.small.v2.spec"]["path"] == ["models", "small.v2", "spec"], str(rows["models.small.v2.spec"]))


def test_a_model_named_with_a_dot_is_saved_by_its_key_parts_defect():
    # A model named with a dot (`qwen3.5-4b`) could not be saved from the Settings page: its key was split at every dot,
    # so the value went under `models.qwen3` and was refused. The page now sends the key's parts, which are used whole
    folder = Path(tempfile.mkdtemp())
    _write_two_connection_files(folder)
    previous = _swap_settings_files(folder)
    try:
        saved = settings_service.save_setting(key="models.small.v2.spec",
                                              new_value="lmstudio:big-model",
                                              key_parts=["models", "small.v2", "spec"])
    finally:
        config.DEFAULT_CONFIG_FILE, config.LOCAL_CONFIG_FILE, config.PLAYBOOK_ROOT = previous
    written = json.loads((folder / "playbook.local.json").read_text(encoding="utf-8"))["models"]
    harness.check("saved under the model's whole name", saved.exit_code == 0
                  and written["small.v2"]["spec"] == "lmstudio:big-model" and "small" not in written,
                  f"{saved.output} {written}")


def test_an_empty_phase_model_is_the_phases_default():
    """The Settings page writes a phase's row empty to give the phase back its default model."""

    settings = config.Settings(phaseModels={"frames_processing": ""}, models={"frame": {"spec": "lmstudio:big-model"}})
    harness.check("the empty name is no name", settings.phase_models == {}
                  and settings.select_phase_model_name("frames_processing") == "frame", str(settings.phase_models))
