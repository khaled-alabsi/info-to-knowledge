"""Every setting, as a front end shows it and changes it: its name as the playbook files write it,
the value in force, which file set it, and the comment above it in `config.py`. A change is checked by building the
settings with it before anything is written, and goes into both default settings files (`playbook.default.json` and
`playbook.local.json`, which hold the same values) or into one profile's file. The files' own notes (`_comment`) and
every other key are kept as they were.
"""

from __future__ import annotations

import ast
import inspect
import json
import typing
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

import config
from models import setting_choice_vocabulary
from models.operation_result import OperationResult
from models.ui_errors import SettingPathError, SettingsFileError
from services import host_model_service, setting_run_value_service

__all__ = ["list_settings", "save_setting", "remove_setting", "read_next_job_settings", "set_host_lister"]

# Sections with entries named by the person (`models.frame`, `models.document`), shown per entry
_KEYED_SECTIONS = ("models",)
# The section naming, per phase, the model it calls: shown as one row per phase that calls a model
_PHASE_MODELS_KEY = "phaseModels"
# A text setting allowed this many characters or more is edited in a box of several lines
_LONG_TEXT_LENGTH = 1_000
# The providers whose host lists its models, as a spec names them before its colon
_LISTED_PROVIDERS = ("lmstudio", "ollama", "openai-compatible")
# The lists a person may go past by typing a value of their own: a host not listed yet, a model not downloaded yet
_OPEN_CHOICE_SOURCES = (setting_choice_vocabulary.HOST_MODELS, setting_choice_vocabulary.HOST_NAMES)

# What lists a host's models: a module-level name with a setter, since the suite runs with no host
HOST_LISTER: Callable[[str, str], list[str] | None] = host_model_service.list_host_models


def set_host_lister(lister: Callable[[str, str], list[str] | None]) -> None:
    global HOST_LISTER
    HOST_LISTER = lister


@lru_cache(maxsize=1)
def _read_field_comments() -> dict[tuple[str, str], str]:
    """The `#` comment lines right above each field of each settings class in `config.py`, by (class, field)."""

    source = Path(inspect.getsourcefile(config)).read_text(encoding="utf-8")
    lines = source.splitlines()
    comments: dict[tuple[str, str], str] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef):
            continue
        for statement in node.body:
            if not isinstance(statement, ast.AnnAssign) or not isinstance(statement.target, ast.Name):
                continue
            above = []
            index = statement.lineno - 2
            while index >= 0 and lines[index].strip().startswith("#"):
                above.insert(0, lines[index].strip().lstrip("#").strip())
                index -= 1
            comments[(node.name, statement.target.id)] = " ".join(line for line in above if line)

    return comments


def _build_key(model: type[BaseModel],
               name: str) -> str:
    """The key a field has in the files: its alias, else the model's generated alias, else its own name."""

    field = model.model_fields[name]
    if field.alias:
        return field.alias
    generator = model.model_config.get("alias_generator")

    return generator(name) if generator else name


def _is_set_in(values: dict,
               key_parts: list[str]) -> bool:
    """Whether a settings file's values set the key at `key_parts`."""

    for part in key_parts:
        if not isinstance(values, dict) or part not in values:
            return False
        values = values[part]

    return True


def _describe_kind(setting_value: Any) -> str:
    if isinstance(setting_value, bool):
        return "bool"
    if isinstance(setting_value, (int, float)):
        return "number"
    if isinstance(setting_value, str) or setting_value is None:
        return "text"

    return "json"


def _is_long_text(field: Any) -> bool:
    """Whether a text setting may hold several lines (a length cap of 1,000 or more, as the extra wishes of a PDF's LLM
    clean up), which the page edits in a box of its own rather than one line."""

    return any(getattr(rule, "max_length", 0) and rule.max_length >= _LONG_TEXT_LENGTH for rule in field.metadata)


def _list_choices(annotation: Any) -> list[str]:
    """The values a setting may take when its type names them (a `Literal`), which the page offers as a list; empty
    for every other setting."""

    if typing.get_origin(annotation) is typing.Literal:
        return [str(value) for value in typing.get_args(annotation)]

    return []


def _find_source(layers: list[tuple[str, Path, dict]],
                 path: list[str]) -> str:
    """The label of the last file that sets the key at `path`, "code" when none does."""

    return next((label for label, _, values in reversed(layers) if _is_set_in(values, path)), "code")


def _list_profile_files() -> list[tuple[str, dict]]:
    """Every settings file a run may read, by its label: the default settings, this machine's, and each profile's."""

    files = [("default settings", config.DEFAULT_CONFIG_FILE), ("this machine", config.LOCAL_CONFIG_FILE)]
    files += [(f"profile {name}", config.build_profile_path(name)) for name in config.list_profile_names()]

    return [(label, config.read_settings_file(path)) for label, path in files]


def _list_host_name_options(settings: config.Settings) -> list[dict]:
    """Each entry of `hosts`, named by its name with its address under it."""

    return [{"value": name, "label": name, "hint": host.url, "group": ""} for name, host in sorted(settings.hosts.items())]


def _list_host_model_options(settings: config.Settings) -> list[dict]:
    """Each model an entry of `hosts` serves, as a spec (`lmstudio:<id>`, `openai-compatible:<id>` for a host named
    after no provider) labelled by its id under its host's heading, every host asked at once so one that does not answer
    costs its timeout once; a host that does not answer has no heading."""

    ordered_hosts = sorted(settings.hosts.items())
    providers = [name if name in _LISTED_PROVIDERS else "openai-compatible" for name, _ in ordered_hosts]
    with ThreadPoolExecutor(max_workers=max(len(ordered_hosts), 1)) as pool:
        host_models = list(pool.map(lambda pair: HOST_LISTER(pair[0], pair[1][1].url), zip(providers, ordered_hosts)))

    return [{"value": f"{provider}:{model_id}", "label": model_id, "hint": "",
             "group": f"{name} at {host.url}", "host": name}
            for provider, (name, host), model_ids in zip(providers, ordered_hosts, host_models)
            for model_id in sorted(model_ids or [])]


def _build_choice_lists(settings: config.Settings) -> dict[str, list[dict]]:
    """For each place a setting's list comes from (`setting_choice_vocabulary`), its options: a value, the label it is
    shown by, a hint under the label, and the heading it stands under."""

    model_name_options = [{"value": name, "label": name, "hint": model.spec, "group": ""}
                          for name, model in sorted(settings.models.items())]

    return {setting_choice_vocabulary.MODEL_NAMES: model_name_options,
            setting_choice_vocabulary.HOST_NAMES: _list_host_name_options(settings),
            setting_choice_vocabulary.HOST_MODELS: _list_host_model_options(settings)}


def _describe_choices(field: Any,
                      field_value: Any,
                      choice_lists: dict[str, list[dict]]) -> dict:
    """The values a row is chosen from: the ones its type names (`choices`), or the options its field's `choicesFrom`
    names (`choice_options`), with the empty value first for a field that may be empty and its own value kept in them;
    and whether a value of one's own may be typed."""

    choice_source = (field.json_schema_extra or {}).get(setting_choice_vocabulary.CHOICES_KEY)
    if choice_source is None:
        return {"choices": _list_choices(field.annotation), "choice_options": None, "is_open_choice": False}
    options = list(choice_lists[choice_source])
    if field.default in ("", None):
        options.insert(0, {"value": "", "label": "(empty)", "hint": "its default", "group": ""})
    current = "" if field_value is None else str(field_value)
    if current not in {option["value"] for option in options}:
        options.append({"value": current, "label": current, "hint": "set in the files, not on any host listed",
                        "group": ""})

    return {"choices": [], "choice_options": options, "is_open_choice": choice_source in _OPEN_CHOICE_SOURCES}


def _build_phase_model_rows(settings: config.Settings,
                            layers: list[tuple[str, Path, dict]],
                            choice_lists: dict[str, list[dict]]) -> list[dict]:
    """One row per phase that calls a model, its value the model `phaseModels` names for it, empty for its default."""

    comment = _read_field_comments().get((config.Settings.__name__, "phase_models"), "")
    rows: list[dict] = []
    for phase, default_name in config.PHASE_DEFAULT_MODELS.items():
        path = [_PHASE_MODELS_KEY, phase]
        default_option = {"value": "", "label": f"default: {default_name}", "hint": "", "group": ""}
        rows.append({"key": ".".join(path), "path": path, "value": settings.phase_models.get(phase, ""), "kind": "text",
                     "source": _find_source(layers, path), "comment": comment, "choices": [],
                     "choice_options": [default_option, *choice_lists[setting_choice_vocabulary.MODEL_NAMES]],
                     "is_open_choice": False})

    return rows


def _walk_model(model: type[BaseModel],
                instance: BaseModel,
                key_parts: list[str],
                layers: list[tuple[str, Path, dict]],
                choice_lists: dict[str, list[dict]]) -> list[dict]:
    """One row per leaf field under `key_parts`, its source the last layer that sets it."""

    comments = _read_field_comments()
    rows: list[dict] = []
    for name in model.model_fields:
        key = _build_key(model, name)
        field_value = getattr(instance, name)
        if isinstance(field_value, BaseModel):
            rows += _walk_model(type(field_value), field_value, [*key_parts, key], layers, choice_lists)
            continue
        if key in _KEYED_SECTIONS and isinstance(field_value, dict):
            for entry, entry_value in field_value.items():
                rows += _walk_model(type(entry_value), entry_value, [*key_parts, key, entry], layers, choice_lists)
            continue
        if key == _PHASE_MODELS_KEY:
            rows += _build_phase_model_rows(instance, layers, choice_lists)
            continue
        path = [*key_parts, key]
        field = model.model_fields[name]
        kind = "long-text" if _is_long_text(field) else _describe_kind(field_value)
        rows.append({"key": ".".join(path), "path": path, "value": field_value, "kind": kind,
                     "source": _find_source(layers, path), "comment": comments.get((model.__name__, name), ""),
                     **_describe_choices(field, field_value, choice_lists)})

    return rows


def _add_model_addresses(rows: list[dict],
                         settings: config.Settings) -> list[dict]:
    """Each model spec row with the host its model runs on (`chosen_host`) and the host named like its provider
    (`default_host`, what an empty `host` means): one model is served by several hosts, so the page ticks only the entry
    under the host in use, and choosing one under another host sets the model's `host` to it."""

    for row in rows:
        path = row.get("path") or []
        if len(path) != 3 or path[0] != "models" or path[2] != "spec":
            continue
        model = settings.resolve_model(path[1])
        provider = model.spec.partition(":")[0]
        default_host = provider if provider in settings.hosts else ""
        row["default_host"] = default_host
        row["chosen_host"] = model.host or default_host

    return rows


def _refuse_unknown_profile(profile: str) -> None:
    """Raise SettingsFileError for a profile named with no file: settings listed or saved for it would be the default
    settings' under its name."""

    if profile and profile not in config.list_profile_names():
        raise SettingsFileError(f"no profile {profile!r}: {config.build_profile_path(profile).name} is not on disk")


def _build_layers(profile: str) -> list[tuple[str, Path, dict]]:
    """The files a run reads, in the order each is read on top of the one before: its label, its path, its values."""

    files = [("default", config.DEFAULT_CONFIG_FILE), ("local", config.LOCAL_CONFIG_FILE)]
    if profile:
        files.append((profile, config.build_profile_path(profile)))

    return [(label, path, config.read_settings_file(path)) for label, path in files]


def _merge_layers(layers: list[tuple[str, Path, dict]]) -> dict:
    merged: dict = {}
    for _, _, values in layers:
        merged = config.merge_settings(merged, values)

    return merged


def _set_value(values: dict,
               key_parts: list[str],
               new_value: Any) -> None:
    for part in key_parts[:-1]:
        values = values.setdefault(part, {})
        if not isinstance(values, dict):
            raise SettingPathError(part)
    values[key_parts[-1]] = new_value


_MISSING = object()


def _read_path(values: Any,
               key_parts: list[str]) -> Any:
    for part in key_parts:
        if not isinstance(values, dict) or part not in values:
            return _MISSING
        values = values[part]
    return values


def _drop_value(values: dict,
                key_parts: list[str]) -> None:
    """The value at `key_parts` taken out, and every group it leaves holding nothing but notes with it"""

    parents = [values]
    for part in key_parts[:-1]:
        child = parents[-1].get(part)
        if not isinstance(child, dict):
            return
        parents.append(child)
    parents[-1].pop(key_parts[-1], None)
    for depth in range(len(key_parts) - 1, 0, -1):
        if any(not name.startswith("_") for name in parents[depth]):
            break
        parents[depth - 1].pop(key_parts[depth - 1], None)


def _drop_values_equal_to(values: dict,
                          default: dict,
                          prefix: list[str] | None = None) -> int:
    """Every value of `values` the same as `default`'s at its place taken out (notes kept); the count taken out"""

    prefix = prefix or []
    dropped = 0
    for name in list(values):
        if name.startswith("_") or name not in values:
            continue
        value, default_value = values[name], _read_path(default, [*prefix, name])
        if isinstance(value, dict) and isinstance(default_value, dict):
            dropped += _drop_values_equal_to(value, default, [*prefix, name])
            if not any(not key.startswith("_") for key in value):
                values.pop(name)
        elif value == default_value:
            values.pop(name)
            dropped += 1
    return dropped


def trim_local_settings() -> OperationResult:
    """The owner's own settings file left holding only what differs from the shipped default settings: a value the same
    as the default file's is taken out, as it changes nothing (the owner: one place per value, and a set holds only what
    it overrides)."""

    try:
        local = _read_raw_file(config.LOCAL_CONFIG_FILE)
        default = config.drop_notes(_read_raw_file(config.DEFAULT_CONFIG_FILE))
    except SettingsFileError as error:
        return OperationResult(2, [str(error)])
    dropped = _drop_values_equal_to(local, default)
    if dropped:
        config.LOCAL_CONFIG_FILE.write_text(json.dumps(local, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return OperationResult(0, [f"{dropped} values the same as the default settings taken out of {config.LOCAL_CONFIG_FILE.name}"])


def _read_raw_file(path: Path) -> dict:
    """A settings file as written, notes included; {} for a file not there yet. A file that does not read as a JSON
    object raises SettingsFileError, so a save never writes over what it holds."""

    if not path.is_file():
        return {}
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SettingsFileError(f"{path.name} does not read as JSON ({exc}); nothing was saved") from exc
    if not isinstance(values, dict):
        raise SettingsFileError(f"{path.name} is not a settings object; nothing was saved")

    return values


def read_next_job_settings(profile: str = "",
                           provider_set: str = "") -> config.Settings:
    """The settings a job started now on `profile` (the default settings when empty) and the provider set `provider_set` (the
    settings' `defaultProviderSet` when empty) would read. A set no `providerSets` entry holds raises `ValueError` naming the
    sets that exist."""

    merged = _merge_layers(_build_layers(profile))
    name = provider_set or merged.get("defaultProviderSet", config.DEFAULT_PROVIDER_SET_NAME)

    return config.Settings(**config.merge_provider_set(merged, name))


def list_settings(profile: str = "") -> list[dict]:
    """Every setting a run on these files would read: the default settings, and `profile`'s on top when one is named. A
    profile with no file raises SettingsFileError."""

    _refuse_unknown_profile(profile)
    layers = _build_layers(profile)
    settings = config.Settings(**_merge_layers(layers))

    rows = _walk_model(config.Settings, settings, [], layers, _build_choice_lists(settings))

    rows = _add_model_addresses(rows, settings)

    return setting_run_value_service.add_run_values(rows=rows, settings=settings)


def save_setting(key: str,
                 new_value: Any,
                 profile: str = "",
                 key_parts: list[str] | None = None) -> OperationResult:
    """`key` (as `budgets.maxAttempts`) set to `new_value` in both default settings files, or in `profile`'s file; refused, with the
    schema's reason, when the settings would not load with it. `key_parts` names the key part by part, as a row's `path`
    does: a model's name may hold a dot (`qwen3.5-4b`), which splitting `key` at its dots would cut."""

    key_parts = key_parts or key.split(".")
    try:
        _refuse_unknown_profile(profile)
    except SettingsFileError as error:
        return OperationResult(2, [str(error)])
    # A change to the default settings goes into the owner's own file alone, never the shipped one: the shipped file is
    # the public default, and writing both made the owner's file a second full copy of it
    targets = [config.build_profile_path(profile)] if profile else [config.LOCAL_CONFIG_FILE]

    # STEP 1: the settings a run would read with the change, built before any file is written. A `_` key is a note,
    # which the schema check never sees, so it is refused here
    note = next((part for part in key_parts if part.startswith("_")), None)
    if note is not None:
        return OperationResult(2, [f"{key} is not a setting: {note} is a note"])
    try:
        changed = {target: _read_raw_file(target) for target in targets}
        for values in changed.values():
            _set_value(values, key_parts, new_value)
        # a value set back to the shipped default leaves the owner's file, which holds only what differs
        if not profile and _read_path(config.drop_notes(_read_raw_file(config.DEFAULT_CONFIG_FILE)), key_parts) == new_value:
            _drop_value(changed[config.LOCAL_CONFIG_FILE], key_parts)
    except SettingPathError as error:
        return OperationResult(2, [f"{key} is not a setting: {error.part} holds a value"])
    except SettingsFileError as error:
        return OperationResult(2, [str(error)])
    layers = [(label, file, config.drop_notes(changed[file]) if file in changed else read_values)
              for label, file, read_values in _build_layers(profile)]
    try:
        config.Settings(**_merge_layers(layers))
    except ValidationError as error:
        return OperationResult(2, [f"{key} = {json.dumps(new_value)} is refused: "
                                   + "; ".join(issue["msg"] for issue in error.errors())])

    # STEP 2: written, every other key and note kept
    for target, values in changed.items():
        target.write_text(json.dumps(values, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return OperationResult(0, [f"{key} = {json.dumps(new_value)} saved in {', '.join(target.name for target in targets)}; "
                               f"the next run reads it"])


def remove_setting(key_parts: list[str],
                   profile: str = "") -> OperationResult:
    """The entry or value at `key_parts` taken out of the owner's own settings file, or out of `profile`'s file; the
    entry a model, a provider set or a host is. An entry the shipped default settings hold cannot be taken out of the
    owner's file (the shipped file is never written), and nothing is written when the settings would not load without
    it."""

    key = ".".join(key_parts)
    try:
        _refuse_unknown_profile(profile)
    except SettingsFileError as error:
        return OperationResult(2, [str(error)])
    note = next((part for part in key_parts if part.startswith("_")), None)
    if note is not None:
        return OperationResult(2, [f"{key} is not a setting: {note} is a note"])
    target = config.build_profile_path(profile) if profile else config.LOCAL_CONFIG_FILE
    try:
        values = _read_raw_file(target)
        shipped = config.drop_notes(_read_raw_file(config.DEFAULT_CONFIG_FILE))
    except SettingsFileError as error:
        return OperationResult(2, [str(error)])
    if not profile and isinstance(_read_path(shipped, key_parts), dict):
        return OperationResult(2, [f"{key} is part of the shipped settings: it can be changed, not removed"])
    if _read_path(values, key_parts) is _MISSING:
        return OperationResult(2, [f"{key} is not in {target.name}: nothing to remove"])
    _drop_value(values, key_parts)
    layers = [(label, file, config.drop_notes(values) if file == target else read_values)
              for label, file, read_values in _build_layers(profile)]
    try:
        config.Settings(**_merge_layers(layers))
    except ValidationError as error:
        return OperationResult(2, [f"{key} is not removed: the settings would not load without it ("
                                   + "; ".join(issue["msg"] for issue in error.errors()) + ")"])
    target.write_text(json.dumps(values, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return OperationResult(0, [f"{key} removed from {target.name}; the next run reads it"])
