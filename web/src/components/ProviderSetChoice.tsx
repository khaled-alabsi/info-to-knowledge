import { useEffect, useState } from "react";
import { fetchJson } from "../api";
import type { PhaseSelection, WorkerSets } from "../api";
import { CHOICE_HELP, ChoiceDropdown } from "./RunChoices";

// The worker sets the settings define, read once; null until the server answers or when it cannot be read, which shows
// no choice: a page with no set to pick has no drop-down
export function useWorkerSets(): WorkerSets | null {
  const [sets, setSets] = useState<WorkerSets | null>(null);
  useEffect(() => {
    fetchJson<WorkerSets>("/worker-sets").then(setSets).catch(() => setSets(null));
  }, []);
  return sets;
}

// A set's name with the machine it runs on, "default · main machine"; the name alone when the settings name no machine,
// or a machine of the set's own name: a set "gpu-box" on the machine "gpu-box" read "gpu-box · gpu-box"
export function nameWorkerSet(entry: { name: string; machine?: string }): string {
  const isOwnName = entry.machine?.trim().toLowerCase() === entry.name.trim().toLowerCase();
  return entry.machine && !isOwnName ? `${entry.name} · ${entry.machine}` : entry.name;
}

// Names a selection's worker set in words: the chosen set, or the one a run with no choice is on
export function describeWorkerSet(selection: PhaseSelection, sets: WorkerSets | null): string {
  return selection.worker_set || (sets ? `${sets.default_set} (default)` : "default");
}

// Which worker set the run (or every ticked run) is on: its own models and hosts. Jobs of one set run one after
// another; jobs of different sets run at the same time. Shown only when the settings define a set besides the default.
export function WorkerSetChoice({ sets, selection, onChange, isDropdown = false }: {
  sets: WorkerSets | null;
  selection: PhaseSelection;
  onChange: (selection: PhaseSelection) => void;
  isDropdown?: boolean;
}) {
  if (!sets || sets.sets.length < 2) return null;
  const defaultEntry = sets.sets.find((entry) => entry.name === sets.default_set);
  const choices = [{ value: "", label: `${nameWorkerSet(defaultEntry ?? { name: sets.default_set })} (default)` },
    ...sets.sets.filter((entry) => entry.name !== sets.default_set).map((entry) => ({ value: entry.name, label: nameWorkerSet(entry) }))];
  const value = selection.worker_set ?? "";
  if (isDropdown) {
    return (
      <ChoiceDropdown label="Worker set" title="Worker set: the models and hosts this run calls" value={value}
        help={CHOICE_HELP.workerSet} isDefault={!value} choices={choices}
        onChange={(next) => onChange({ ...selection, worker_set: next })} />
    );
  }
  return (
    <div className="field" title="Worker set: the models and hosts this run calls">
      <span>Worker set</span>
      <div className="phase-list" role="radiogroup" aria-label="Worker set">
        {choices.map((choice) => (
          <button key={choice.value} role="radio" aria-checked={value === choice.value}
            className={value === choice.value ? "on" : ""}
            onClick={() => onChange({ ...selection, worker_set: choice.value })}>{choice.label}</button>
        ))}
      </div>
    </div>
  );
}
