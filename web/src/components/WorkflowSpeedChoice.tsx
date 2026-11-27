import { Dropdown } from "./Dropdown";
import Icon from "./Icon";
import type { PhaseSelection, RunDefaults, SpeedOptions, SpeedValue } from "../api";
import { nameSpeedPreset } from "../speed";

// The values a selection runs with: its preset's, with what was changed by hand on top
function readValues(selection: PhaseSelection, options: SpeedOptions): Record<string, SpeedValue> {
  const preset = selection.speed_preset || options.default_preset;
  return { ...options.current, ...(options.presets[preset] ?? {}), ...(selection.speed_settings ?? {}) };
}

// The workflow a selection's speed takes ("quality", "direct-draft" or "fast-processing"); "" before the menu is known
export function readSpeedWorkflow(selection: PhaseSelection, defaults: RunDefaults | null): string {
  const options = defaults?.speed;
  return options ? String(readValues(selection, options)["video.workflow"] ?? "") : "";
}

// The phases a selection's speed switches off, as `/api/phases` takes them: Balanced has no review and no critique
// collection (the owner), and a speed changed by hand may switch either off
export function readSpeedOffPhases(selection: PhaseSelection, defaults: RunDefaults | null): Record<string, string> {
  const options = defaults?.speed;
  if (!options) return {};
  const values = readValues(selection, options);
  return { ...(values["review.isOn"] === false ? { review: "off" } : {}),
    ...(values["critiqueCollection.isOn"] === false ? { critique_collection: "off" } : {}) };
}

// The speed's name: its preset's while every value is the preset's own, else "Custom" (the owner: "when the user change
// some value ... the name will change ... to custom")
export function describeSpeed(selection: PhaseSelection, defaults: RunDefaults | null): string {
  const options = defaults?.speed;
  if (!options) return "";
  const preset = selection.speed_preset || options.default_preset;
  const values = readValues(selection, options);
  const presetValues = { ...options.current, ...(options.presets[preset] ?? {}) };
  const isPreset = Object.keys(values).every((key) => values[key] === presetValues[key]);
  return isPreset ? nameSpeedPreset(preset) : "Custom";
}

// The Workflow speed menu (the owner: "a drop-down ... shows parameter that affect the speed"): the presets on top, each
// setting under them with what it does; a preset chosen sets them all, a setting changed makes the speed "Custom".
// It sends the preset and only the values changed from it, so a run line names what the person chose
export default function WorkflowSpeedChoice({ selection, onChange, defaults }: { selection: PhaseSelection;
  onChange: (selection: PhaseSelection) => void; defaults: RunDefaults | null }) {
  const options = defaults?.speed;
  if (!options) return null;
  const preset = selection.speed_preset || options.default_preset;
  const values = readValues(selection, options);
  const name = describeSpeed(selection, defaults);
  const presetValues = { ...options.current, ...(options.presets[preset] ?? {}) };
  const setValue = (key: string, value: SpeedValue) => {
    const changes = { ...(selection.speed_settings ?? {}), [key]: value };
    // a value set back to the preset's own is no change
    if (presetValues[key] === value) delete changes[key];
    onChange({ ...selection, speed_preset: preset, speed_settings: changes });
  };
  return (
    <Dropdown label={name} variant="small" ariaLabel={`workflow speed: ${name}`}
      title="the settings that change how long the run takes" panelClassName="speed-panel">
      {() => (
        <div className="menu speed-menu">
          <div role="radiogroup" aria-label="workflow speed preset">
            {Object.keys(options.presets).map((entry) => {
              const isChosen = entry === preset && name !== "Custom";
              return (
                <button key={entry} type="button" role="radio" aria-checked={isChosen}
                  onClick={() => onChange({ ...selection, speed_preset: entry, speed_settings: {} })}>
                  {isChosen ? <Icon name="check" size={14} /> : <span className="icon-space" />}
                  <span className="menu-text">{nameSpeedPreset(entry)}
                    <small>{entry === preset && name === "Custom" ? "changed by hand: choose it to set its values again"
                      : describePreset(entry)}</small></span>
                </button>
              );
            })}
          </div>
          <div className="speed-settings">
            {options.settings.map((setting) => {
              const value = values[setting.key];
              if (setting.kind === "boolean") {
                return (
                  <button key={setting.key} type="button" role="switch" aria-checked={value === true}
                    onClick={() => setValue(setting.key, value !== true)}>
                    <span className="menu-text">{setting.label}<small>{setting.hint}</small></span>
                    <span className="menu-switch" />
                  </button>
                );
              }
              if (setting.kind === "number") {
                return (
                  <label key={setting.key} className="speed-number">
                    <span className="menu-text">{setting.label}<small>{setting.hint}</small></span>
                    <input type="number" min={setting.min} max={setting.max} value={Number(value)}
                      aria-label={setting.label}
                      onChange={(event) => setValue(setting.key, Number(event.target.value))} />
                  </label>
                );
              }
              return (
                <div key={setting.key} className="speed-choice">
                  <span className="menu-text">{setting.label}<small>{setting.hint}</small></span>
                  <div className="segmented" role="radiogroup" aria-label={setting.label}>
                    {setting.options.map((option) => (
                      <button key={option.value} type="button" role="radio" aria-checked={value === option.value}
                        className={value === option.value ? "on" : ""} onClick={() => setValue(setting.key, option.value)}>
                        {option.label}
                      </button>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </Dropdown>
  );
}

// What each shipped preset is for, under its name; a preset the settings add has no line
function describePreset(preset: string): string {
  if (preset === "quality") return "the large model, with critique, repairs, review and the rule set";
  if (preset === "balanced") return "the large model with no critique, no repair, no review and no rule set";
  if (preset === "fast") return "the large model writes the document straight from the frames, 8 frames a call, with no check";
  return "";
}
