"""A video run's Workflow speed: the preset chosen for it and the values it sets, kept in
`run-speed.json` and laid over the settings at every start of the run, so a resume runs as it began. A value changed by
hand makes the speed "custom", named after the preset it started from."""

from __future__ import annotations

from pathlib import Path

import config
from models import output_contract
from models import speed_setting_vocabulary
from services import video_workflow_service
from utils import json_store_utils

__all__ = ["read_chosen_speed", "resolve_speed", "write_chosen_speed", "apply_chosen_speed", "list_speed_values",
           "describe_speed_options", "parse_speed_setting", "resolve_next_workflow", "CHOSEN_BY_SPEED"]

# The preset a new video run takes when nobody chose a speed for it (the owner)
DEFAULT_PRESET = "balanced"

# Who chose a run's workflow when its speed set it, and when the person set it on the run line, as `run-workflow.json`
# records it
CHOSEN_BY_SPEED = "workflow speed"
_CHOSEN_BY_USER = "user"


def read_chosen_speed(folder: Path) -> dict | None:
    """`{"preset", "values"}` as the run folder keeps it, None when nobody chose one."""

    path = folder / output_contract.RUN_SPEED_FILE

    return json_store_utils.read_json(path) if path.is_file() else None


def _name_speed(preset: str,
                values: dict) -> str:
    """The preset's name when `values` are its own, else "custom"."""

    preset_values = config.settings.speed_presets.get(preset, {})
    is_preset = all(preset_values.get(key) == value for key, value in values.items())

    return preset if is_preset else speed_setting_vocabulary.CUSTOM


def resolve_speed(preset: str,
                  changes: dict) -> dict:
    """`{"preset", "values", "name"}`: the preset's values with `changes` on top, and the name the run shows. Raises
    `ValueError` naming an unknown preset or setting."""

    presets = config.settings.speed_presets
    if preset not in presets:
        raise ValueError(f"no workflow speed preset {preset!r}: the presets are {', '.join(presets)}")
    unknown = sorted(set(changes) - set(speed_setting_vocabulary.SPEED_KEYS))
    if unknown:
        raise ValueError(f"{', '.join(unknown)} is no setting of the Workflow speed menu")
    values = {**presets[preset], **changes}

    # `changes` is what the person set by hand on top of the preset; the run keeps it apart, so a preset edited later
    # reaches a run that never changed a value (it stays "Balanced" and runs with Balanced's current values)
    return {"preset": preset, "values": values, "name": _name_speed(preset, values), "changes": dict(changes)}


def write_chosen_speed(folder: Path,
                       speed: dict,
                       is_workflow_kept: bool = True) -> None:
    """The run's speed, and its workflow when the speed sets one, so every reader of the workflow agrees. A live run
    passes `is_workflow_kept=False`: its workflow is read while it works, so the new one waits for its next start."""

    json_store_utils.write_json(folder / output_contract.RUN_SPEED_FILE, speed)
    workflow = speed["values"].get("video.workflow")
    if workflow and is_workflow_kept:
        video_workflow_service.write_chosen_workflow(folder, str(workflow), CHOSEN_BY_SPEED)


def list_speed_values(speed: dict) -> dict:
    """The values a run with the kept `speed` runs with: its preset as it is now with what was changed by hand on top.
    A speed kept before changes were kept apart holds its stored values over the preset's; a preset the settings no
    longer hold leaves the stored values."""

    stored = speed.get("values") or {}
    preset_values = config.settings.speed_presets.get(speed.get("preset") or "")
    if preset_values is None:
        return dict(stored)
    # the preset as it is now, so a setting added to the menu after the run chose its speed takes the preset's value,
    # never the program's
    return {**preset_values, **(speed["changes"] if "changes" in speed else stored)}


def resolve_next_workflow(folder: Path,
                          workflow_flag: str,
                          chosen_speed: dict | None) -> str:
    """The workflow the run's next start takes: the run line's `--workflow`, else the speed the run line chose, else the
    speed the folder kept unless the person chose the folder's workflow by hand, else the folder's workflow."""

    if workflow_flag:
        return workflow_flag
    kept_workflow = video_workflow_service.read_chosen_workflow(folder)
    is_chosen_by_hand = kept_workflow is not None and kept_workflow.get("chosenBy") == _CHOSEN_BY_USER
    speed = chosen_speed or (None if is_chosen_by_hand else read_chosen_speed(folder))
    # the values the speed was chosen with, never its preset as it is now: a run that chose the Fast speed when Fast
    # meant the earlier fast workflow keeps that workflow
    speed_workflow = (speed or {}).get("values", {}).get("video.workflow")

    return str(speed_workflow) if speed_workflow else video_workflow_service.resolve_workflow(folder)


def apply_chosen_speed(folder: Path) -> dict | None:
    """The run's kept speed laid over the settings in force, for this process; the speed, None when it has none."""

    speed = read_chosen_speed(folder)
    if speed:
        config.apply_speed_values(list_speed_values(speed))

    return speed


def parse_speed_setting(pair: str) -> tuple[str, bool | int | str]:
    """`framesProcessing.isCritiqueOn=false` as its key and its value, read by the setting's kind."""

    key, separator, text = pair.partition("=")
    setting = next((entry for entry in speed_setting_vocabulary.SPEED_SETTINGS if entry["key"] == key), None)
    if not separator or setting is None:
        raise ValueError(f"{pair!r} is no KEY=VALUE of the Workflow speed menu's settings")
    if setting["kind"] == speed_setting_vocabulary.BOOLEAN:
        if text not in ("true", "false"):
            raise ValueError(f"{key} takes true or false, not {text!r}")
        return key, text == "true"
    if setting["kind"] == speed_setting_vocabulary.NUMBER:
        if not text.isdigit():
            raise ValueError(f"{key} takes a whole number, not {text!r}")
        return key, int(text)

    return key, text


def _read_setting(dumped: dict,
                  key: str) -> bool | int | str:
    """A setting's value in the dumped settings `dumped`, by its playbook name."""

    value = dumped
    for part in key.split("."):
        value = value[part]

    return value


def describe_speed_options(settings: config.Settings | None = None) -> dict:
    """What the page's Workflow speed menu lists: each setting with what it does, each preset with its values, and the
    preset a run takes when none is chosen (the one whose values `settings`, else those in force, hold)."""

    in_force = settings or config.settings
    rows = [{**setting, "options": [{"value": value, "label": label} for value, label in setting.get("options", ())]}
            for setting in speed_setting_vocabulary.SPEED_SETTINGS]
    dumped = in_force.model_dump(by_alias=True)
    current = {key: _read_setting(dumped, key) for key in speed_setting_vocabulary.SPEED_KEYS}
    presets = in_force.speed_presets
    # the owner: a run nobody chose a speed for takes Balanced
    default = (DEFAULT_PRESET if DEFAULT_PRESET in presets else
               next((name for name, values in presets.items()
                     if all(current.get(key) == value for key, value in values.items())), speed_setting_vocabulary.CUSTOM))

    return {"settings": rows, "presets": presets, "current": current, "default_preset": default}
