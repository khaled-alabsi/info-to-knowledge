import { useState } from "react";
import type { SettingOption, SettingRow } from "../api";
import { asTree, canModelDo, DEFAULT_SET, describeModelRun, isTree, listJobs, listModelNames, MODELS_KEY, readDefaultJobModel,
  readModelProvider, readPath, readSetJobModel, SETS_KEY } from "../modelLibrary";
import type { Job, Tree } from "../modelLibrary";
import { describeHost, describeValue, isEmptyValue, isOwnerChange } from "../settingRunValue";
import { readSettingLabel } from "../settingsLabels";
import Icon from "./Icon";
import OptionMenu from "./OptionMenu";
import type { SettingDrafts } from "./SettingsView";

// The worker sets as the owner thinks of them: a set only links each job (a phase, or a job a provider does such as speech
// to text) to a model of the models library, one menu a job, each listing only the models that can do that job. The
// machine a set runs on is not chosen: it is the provider its models run on. The default set is the settings as they are;
// every other set holds only what it changes, a job it leaves out staying the default set's. One card a set, built from
// the settings that hold them: `workerSets` (each set's links), `defaultWorkerSet` (the set a job takes when none is
// chosen) and `machine` (an older file's machine of the default set, still read but no longer shown or written). Every
// card has a Copy button: it makes a new set from the card's links and asks for its name.

const CHOSEN_KEY = "defaultWorkerSet";
const MACHINE_KEY = "machine";
export const WORKER_SET_KEYS = new Set([SETS_KEY, CHOSEN_KEY, MACHINE_KEY]);

type Leaf = { path: string[]; value: unknown };

// every value a set changes, by its path: { models: { frame: { spec: "x" } } } is models.frame.spec = "x"
function listLeaves(node: unknown, path: string[] = []): Leaf[] {
  if (isTree(node) && Object.keys(node).length > 0) {
    return Object.entries(node).flatMap(([key, child]) => listLeaves(child, [...path, key]));
  }
  return [{ path, value: node }];
}

function setLeaf(node: Tree, path: string[], value: unknown): Tree {
  const [head, ...rest] = path;
  if (rest.length === 0) return { ...node, [head]: value };
  return { ...node, [head]: setLeaf(asTree(node[head]), rest, value) };
}

// the value taken out, and every group it leaves empty with it
function dropLeaf(node: Tree, path: string[]): Tree {
  const [head, ...rest] = path;
  const { [head]: child, ...others } = node;
  if (rest.length === 0 || !isTree(child)) return others;
  const kept = dropLeaf(child, rest);
  return Object.keys(kept).length === 0 ? others : { ...others, [head]: kept };
}

// A value typed in a box: empty when there is none, never a word standing in for it
function renderValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

// The default's value at `path`: the setting holding it (its row, when the setting is that very path), or a part of a
// setting whose value is a group
function findDefault(rows: SettingRow[], path: string[]): { found: boolean; value: unknown; row: SettingRow | null } {
  for (let length = path.length; length > 0; length -= 1) {
    const prefix = path.slice(0, length).join("\u0001");
    const row = rows.find((candidate) => readPath(candidate).join("\u0001") === prefix);
    if (!row) continue;
    let value: unknown = row.value;
    for (const part of path.slice(length)) value = isTree(value) ? value[part] : undefined;
    return { found: true, value, row: length === path.length ? row : null };
  }
  return { found: false, value: undefined, row: null };
}

// A typed value kept in the default's type: a number stays a number, true and false stay booleans
function parseTyped(text: string, like: unknown): unknown {
  if (typeof like === "number" && text.trim() !== "" && !Number.isNaN(Number(text))) return Number(text);
  if (typeof like === "boolean" && (text === "true" || text === "false")) return text === "true";
  return text;
}

function readJson(rows: SettingRow[], drafts: SettingDrafts, key: string): Tree {
  const draft = drafts[key];
  if (draft) {
    try {
      const parsed: unknown = JSON.parse(String(draft.raw));
      if (isTree(parsed)) return parsed;
    } catch {
      // a draft typed by hand that is no JSON: the saved value stands until it is fixed
    }
  }
  return asTree(rows.find((row) => row.key === key)?.value);
}

// The model a job of the default set calls, as it stands on the page: the model chosen and not saved yet, else the saved one
function readDefaultChoice(job: Job, drafts: SettingDrafts): string {
  const draft = drafts[job.key];
  return draft ? String(draft.raw) : typeof job.row.value === "string" ? job.row.value : "";
}

// Where a model runs, in the words a person uses: "lmstudio · qwen3.5-4b". A job with no model runs the way the program
// always has when a provider does it (speech to text, ...), and has no model when it is a phase
function describeRun(rows: SettingRow[], model: string, isProviderJob = false): string {
  if (!model) return isProviderJob ? "this computer, the program's own way" : "no model chosen";
  const run = describeModelRun(rows, model);
  return `${run.host} · ${run.model}`;
}

// What a copy of the default set holds: every job its owner linked to a model, the shipped links staying the shipped ones
function buildDefaultSetCopy(jobs: Job[], drafts: SettingDrafts): Tree {
  let copy: Tree = {};
  for (const job of jobs) {
    const choice = readDefaultChoice(job, drafts);
    if (choice !== "" && (drafts[job.key] || isOwnerChange(job.row.source))) copy = setLeaf(copy, job.path, choice);
  }
  return copy;
}

export default function WorkerSetsPanel({ rows, drafts, onEdit }: {
  rows: SettingRow[];
  drafts: SettingDrafts;
  onEdit: (row: SettingRow, raw: string) => void;
}) {
  const [newSet, setNewSet] = useState("");
  // the card whose Copy was pressed, and the name typed for the copy
  const [copying, setCopying] = useState<{ from: string; name: string } | null>(null);
  const setsRow = rows.find((row) => row.key === SETS_KEY);
  const chosenRow = rows.find((row) => row.key === CHOSEN_KEY);
  if (!setsRow) return null;

  const sets = readJson(rows, drafts, SETS_KEY);
  const chosen = String(drafts[CHOSEN_KEY]?.raw ?? chosenRow?.value ?? DEFAULT_SET) || DEFAULT_SET;
  const names = [DEFAULT_SET, ...Object.keys(sets)];
  const modelNames = listModelNames(rows);
  const jobs = listJobs(rows);
  const jobPaths = new Set(jobs.map((job) => job.path.join("\u0001")));

  const writeSets = (next: Tree) => onEdit(setsRow, JSON.stringify(next, null, 2));
  const takenNames = new Set(names);
  const newSetName = newSet.trim();
  const isNewNameTaken = takenNames.has(newSetName);
  const addSet = () => {
    if (!newSetName || isNewNameTaken) return;
    writeSets({ ...sets, [newSetName]: {} });
    setNewSet("");
  };
  const removeSet = (name: string) => {
    writeSets(dropLeaf(sets, [name]));
    if (chosenRow && chosen === name) onEdit(chosenRow, DEFAULT_SET);
  };
  const copyName = copying?.name.trim() ?? "";
  const copySet = () => {
    if (!copying || !copyName || takenNames.has(copyName)) return;
    const source = copying.from === DEFAULT_SET
      ? buildDefaultSetCopy(jobs, drafts)
      : (JSON.parse(JSON.stringify(asTree(sets[copying.from]))) as Tree);
    writeSets({ ...sets, [copyName]: source });
    setCopying(null);
  };
  // The models a job may be linked to: the library's names, each with where it runs; a model the set names that the library
  // no longer holds stays listed, so the link still shows
  // (only the models whose provider can do the job: a speech provider's model is no choice for a phase)
  // The options are grouped by the server that runs the model, each named "gpu-box: model"; the servers stand in the order the
  // settings list them, the models no listed server runs last
  const serverOrder = Object.keys(asTree(rows.find((row) => row.key === "hosts")?.value));
  const readServerRank = (model: string) => {
    const index = serverOrder.indexOf(readModelProvider(rows, model));
    return index < 0 ? serverOrder.length : index;
  };
  const listChoices = (current: string, firstOption: SettingOption | null, job: Job): SettingOption[] => [
    ...(firstOption ? [firstOption] : []),
    ...modelNames.filter((model) => model === current || canModelDo(rows, model, job))
      .sort((first, second) => readServerRank(first) - readServerRank(second))
      .map((model) => {
        const server = readModelProvider(rows, model);
        const group = server ? describeHost(server) : "Other";
        return { value: model, label: `${group}: ${model}`, hint: describeRun(rows, model), group };
      }),
    ...(current && !modelNames.includes(current)
      ? [{ value: current, label: current, hint: "not in the models library", group: "Other" }] : []),
  ];

  return (
    <div className="worker-sets">
      <p className="muted">A worker set gives each job a model. Each job's menu lists, by server, only the models that can do
        that job. The default set is the settings as they are. Every other set lists only the jobs it changes; a job it does
        not list uses the default set's model.</p>
      {chosenRow && (
        <div className="worker-set-chosen">
          <OptionMenu name="Used when a job names no set" prefix="Used when a job names no set:" value={chosen}
            options={names.map((name) => ({ value: name, label: name, hint: "", group: "" }))}
            onChoose={(name) => onEdit(chosenRow, name)} />
        </div>
      )}
      {names.map((name) => {
        const isDefault = name === DEFAULT_SET;
        const overrides = asTree(sets[name]);
        const otherLeaves = isDefault ? [] : listLeaves(overrides)
          .filter((leaf) => leaf.path.length > 0 && !(leaf.path.length === 1 && leaf.path[0] === MACHINE_KEY)
            && !jobPaths.has(leaf.path.join("\u0001")));
        const isCopying = copying?.from === name;
        return (
          <div key={name} className="worker-set" aria-label={`worker set ${name}`} role="group">
            <div className="worker-set-head">
              <h4>{name}</h4>
              <div className="worker-set-actions">
                <button type="button" className="btn" onClick={() => setCopying({ from: name, name: "" })}
                  aria-label={`copy worker set ${name}`}><Icon name="copy" size={14} /> Copy</button>
                {!isDefault && (
                  <button type="button" className="btn danger" onClick={() => removeSet(name)}
                    aria-label={`remove worker set ${name}`}><Icon name="trash" size={14} /> Remove</button>
                )}
              </div>
            </div>
            {isCopying && (
              <div className="worker-set-add copy-form">
                <input type="text" aria-label={`name of the copy of ${name}`} placeholder={`name of the copy of ${name}`}
                  autoFocus value={copying.name} onChange={(event) => setCopying({ ...copying, name: event.target.value })}
                  onKeyDown={(event) => { if (event.key === "Enter") copySet(); }} />
                <button type="button" className="btn primary" onClick={copySet}
                  disabled={!copyName || takenNames.has(copyName)}>Create copy</button>
                <button type="button" className="btn" onClick={() => setCopying(null)}>Cancel</button>
                {takenNames.has(copyName) && <small className="error">a set has this name</small>}
              </div>
            )}
            <div className="job-links" role="group" aria-label={`jobs of ${name}`}>
              {jobs.map((job) => {
                const defaultChoice = readDefaultChoice(job, drafts) || readDefaultJobModel(job);
                const current = isDefault ? readDefaultChoice(job, drafts) : readSetJobModel(overrides, job);
                const usual = typeof job.row.resolved === "string" ? job.row.resolved : "";
                const isProviderJob = job.key.startsWith("providers.");
                const firstOption: SettingOption | null = isDefault
                  ? (job.key.startsWith("phaseModels.") ? { value: "", label: `the ${usual} model`, hint: "", group: "" }
                    : isProviderJob ? { value: "", label: "none: the program's own way", hint: "", group: "" } : null)
                  : { value: "", label: `same as default set: ${defaultChoice || "none"}`, hint: "", group: "" };
                const effective = current || defaultChoice;
                return (
                  <div key={job.key} className="job-link">
                    <span className="job-title">{job.title}</span>
                    <OptionMenu name={`${name}: ${job.title}`} value={current} options={listChoices(current, firstOption, job)}
                      onChoose={(model) => {
                        if (isDefault) {
                          onEdit(job.row, model);
                          return;
                        }
                        writeSets({ ...sets, [name]: model === "" ? dropLeaf(overrides, job.path) : setLeaf(overrides, job.path, model) });
                      }} />
                    <small className="muted job-runs">Runs on {describeRun(rows, effective, isProviderJob)}</small>
                  </div>
                );
              })}
            </div>
            {isDefault ? (
              <p className="muted">Every other value of this set is a setting in the sections below.</p>
            ) : (
              <>
                {jobs.every((job) => readSetJobModel(overrides, job) === "") && otherLeaves.length === 0 && (
                  <p className="muted">It changes nothing yet: it runs exactly like the default set.</p>
                )}
                {otherLeaves.length > 0 && <h5 className="worker-set-subhead">Other values this set changes</h5>}
                {otherLeaves.map((leaf) => {
                  const fallback = findDefault(rows, leaf.path);
                  const key = leaf.path.join(".");
                  const label = leaf.path[0] === MODELS_KEY && leaf.path.length === 3
                    ? `${leaf.path[1]}: ${readSettingLabel(key)}` : readSettingLabel(key);
                  return (
                    <div key={key} className="worker-set-value">
                      <span className="job-title">{label}</span>
                      <input type="text" aria-label={`${name}: ${key}`} value={renderValue(leaf.value)}
                        onChange={(event) => writeSets({ ...sets, [name]: setLeaf(overrides, leaf.path,
                          parseTyped(event.target.value, fallback.found ? fallback.value : leaf.value)) })} />
                      <small className="muted">default: {fallback.found
                        ? describeValue(fallback.row, isEmptyValue(fallback.value) ? null : fallback.value)
                        : "not a known setting"}</small>
                      <button type="button" className="btn ghost" aria-label={`stop changing ${key} in ${name}`}
                        onClick={() => writeSets({ ...sets, [name]: dropLeaf(overrides, leaf.path) })}>Use default</button>
                    </div>
                  );
                })}
              </>
            )}
          </div>
        );
      })}
      <div className="worker-set-add">
        <input type="text" aria-label="new worker set name" placeholder="new set's name" value={newSet}
          onChange={(event) => setNewSet(event.target.value)} />
        <button type="button" className="btn" onClick={addSet} disabled={!newSetName || isNewNameTaken}>+ Add worker set</button>
        {newSetName && isNewNameTaken && <small className="error">a set has this name</small>}
      </div>
    </div>
  );
}
