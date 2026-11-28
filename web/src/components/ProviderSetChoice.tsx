import { useEffect, useState } from "react";
import { fetchJson } from "../api";
import type { PhaseSelection, ProviderSets } from "../api";
import { CHOICE_HELP, ChoiceDropdown } from "./RunChoices";

// The provider sets the settings define, read once; null until the server answers or when it cannot be read, which shows
// no choice: a page with no set to pick has no drop-down
export function useProviderSets(): ProviderSets | null {
  const [sets, setSets] = useState<ProviderSets | null>(null);
  useEffect(() => {
    fetchJson<ProviderSets>("/provider-sets").then(setSets).catch(() => setSets(null));
  }, []);
  return sets;
}

// A set's name with the machine it runs on, "default · main machine"; the name alone when the settings name no machine,
// or a machine of the set's own name: a set "gpu-box" on the machine "gpu-box" read "gpu-box · gpu-box"
export function nameProviderSet(entry: { name: string; machine?: string }): string {
  const isOwnName = entry.machine?.trim().toLowerCase() === entry.name.trim().toLowerCase();
  return entry.machine && !isOwnName ? `${entry.name} · ${entry.machine}` : entry.name;
}

// Names a selection's provider set in words: the chosen set, or the one a run with no choice is on
export function describeProviderSet(selection: PhaseSelection, sets: ProviderSets | null): string {
  return selection.provider_set || (sets ? `${sets.default_set} (default)` : "default");
}

// Which provider set the run (or every ticked run) is on: its own models and hosts. Jobs of one set run one after
// another; jobs of different sets run at the same time. Shown only when the settings define a set besides the default.
export function ProviderSetChoice({ sets, selection, onChange, isDropdown = false }: {
  sets: ProviderSets | null;
  selection: PhaseSelection;
  onChange: (selection: PhaseSelection) => void;
  isDropdown?: boolean;
}) {
  if (!sets || sets.sets.length < 2) return null;
  const defaultEntry = sets.sets.find((entry) => entry.name === sets.default_set);
  const choices = [{ value: "", label: `${nameProviderSet(defaultEntry ?? { name: sets.default_set })} (default)` },
    ...sets.sets.filter((entry) => entry.name !== sets.default_set).map((entry) => ({ value: entry.name, label: nameProviderSet(entry) }))];
  const value = selection.provider_set ?? "";
  if (isDropdown) {
    return (
      <ChoiceDropdown label="Provider set" title="Provider set: the models and hosts this run calls" value={value}
        help={CHOICE_HELP.providerSet} isDefault={!value} choices={choices}
        onChange={(next) => onChange({ ...selection, provider_set: next })} />
    );
  }
  return (
    <div className="field" title="Provider set: the models and hosts this run calls">
      <span>Provider set</span>
      <div className="phase-list" role="radiogroup" aria-label="Provider set">
        {choices.map((choice) => (
          <button key={choice.value} role="radio" aria-checked={value === choice.value}
            className={value === choice.value ? "on" : ""}
            onClick={() => onChange({ ...selection, provider_set: choice.value })}>{choice.label}</button>
        ))}
      </div>
    </div>
  );
}
