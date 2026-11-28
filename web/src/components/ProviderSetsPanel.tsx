import { useState } from "react";
import type { SettingOption, SettingRow } from "../api";
import { asTree, canModelDo, DEFAULT_SET, describeModelRun, describeModelWait, isTree, listJobs, listModelNames, MODELS_KEY, readDefaultJobModel,
  readModelProvider, readPath, readSetJobModel, SETS_KEY } from "../modelLibrary";
import type { Job, Tree } from "../modelLibrary";
import { describeHost, describeValue, isEmptyValue, isOwnerChange, readOptionLabel } from "../settingRunValue";
import { readSettingLabel } from "../settingsLabels";
import { Menu } from "./Dropdown";
import Icon from "./Icon";
import OptionMenu from "./OptionMenu";
import type { SettingDrafts } from "./SettingsView";

// The provider sets as the owner thinks of them: a set only links each job (a phase, or a job a provider does such as speech
// to text) to a model of the models library, one menu a job, each listing only the models that can do that job. The
// machine a set runs on is not chosen: it is the provider its models run on. The default set is the settings as they are;
// every other set holds only what it changes, a job it leaves out staying the default set's. One card a set, built from
// the settings that hold them: `providerSets` (each set's links), `defaultProviderSet` (the set a job takes when none is
// chosen) and `machine` (an older file's machine of the default set, still read but no longer shown or written). Every
// card has a Copy button: it makes a new set from the card's links and asks for its name.

const CHOSEN_KEY = "defaultProviderSet";
const MACHINE_KEY = "machine";
export const PROVIDER_SET_KEYS = new Set([SETS_KEY, CHOSEN_KEY, MACHINE_KEY]);

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

// A setting's value as text, "" when the settings hold none
function readText(rows: SettingRow[], key: string): string {
  const value = rows.find((row) => row.key === key)?.value;
  return typeof value === "string" ? value : "";
}

// The tool a provider job runs on this computer when no model server is chosen for it, by its real name
export function describeBuiltInTool(rows: SettingRow[], jobKey: string): string {
  const kind = PROVIDER_KIND.exec(jobKey)?.[1] ?? "";
  if (kind === "speech") {
    const engine = readText(rows, "audio.engine") || "mlx";
    const model = readText(rows, "audio.whisperModel");
    return `Whisper on ${engine === "mlx" ? "MLX" : engine}${model ? `, ${model}` : ""}`;
  }
  if (kind === "diarization") return readText(rows, "audio.diarization.model") || "pyannote/speaker-diarization-community-1";
  if (kind === "embeddings") return "SigLIP 2 (the frame classifier)";
  if (kind === "decision") return readText(rows, "frames.knowledgeFilterModel") || "google-SigLIP-2";
  return "no model chosen";
}

// A model by its real name and the provider it runs on: "qwen3.6-35b-a3b-mtp on LM Studio"
function describeModelOnProvider(rows: SettingRow[], model: string): string {
  const run = describeModelRun(rows, model);
  return `${run.model} on ${run.host}`;
}

// Where a model runs, in the words a person uses: the server it is on. A provider job with no model runs its built-in
// tool on this computer, named; a phase with no model has none
function describeRun(rows: SettingRow[], model: string, jobKey = ""): string {
  if (!model) return jobKey.startsWith("providers.") ? `this computer: ${describeBuiltInTool(rows, jobKey)}` : "no model chosen";
  return describeModelRun(rows, model).host;
}

// The grey line under a job: where it runs, left out when the chosen option already names that server ("... on
// lmstudio", then "Runs on lmstudio" said it twice), and how long it waits for a reply
export function describeJobLine(rows: SettingRow[], model: string, jobKey: string, shownLabel: string): string {
  const place = describeRun(rows, model, jobKey);
  const isPlaceShown = shownLabel.endsWith(` on ${place}`) || place.endsWith(`: ${shownLabel}`);
  const wait = model ? describeModelWait(rows, model) : "";
  return [isPlaceShown ? "" : `Runs on ${place}`, wait].filter((part) => part !== "").join(" · ");
}

const PROVIDER_KIND = /^providers\.([a-z]+)\./;

// The name the model has on its server, when it is not the name the models list gives it
function describeServerModel(rows: SettingRow[], model: string): string {
  const name = describeModelRun(rows, model).model;
  return name === model ? "" : name;
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

export default function ProviderSetsPanel({ rows, drafts, onEdit }: {
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
  // each model by its name, under its server's heading, with the name it has on that server under it
  const listChoices = (current: string, firstOption: SettingOption | null, job: Job): SettingOption[] => [
    ...(firstOption ? [firstOption] : []),
    ...modelNames.filter((model) => model === current || canModelDo(rows, model, job))
      .sort((first, second) => readServerRank(first) - readServerRank(second))
      .map((model) => {
        const server = readModelProvider(rows, model);
        const group = server ? describeHost(server) : "Other";
        return { value: model, label: model, hint: describeServerModel(rows, model) || "same name on the server", group };
      }),
    ...(current && !modelNames.includes(current)
      ? [{ value: current, label: current, hint: "not in the models list", group: "Other" }] : []),
  ];

  return (
    <div className="provider-sets">
      <p className="muted">A provider set gives each job a model. The default set holds every job. Any other set holds only the
        jobs it changes; every job it leaves out uses the default set's model.</p>
      {names.map((name) => {
        const isDefault = name === DEFAULT_SET;
        const overrides = asTree(sets[name]);
        const otherLeaves = isDefault ? [] : listLeaves(overrides)
          .filter((leaf) => leaf.path.length > 0 && !(leaf.path.length === 1 && leaf.path[0] === MACHINE_KEY)
            && !jobPaths.has(leaf.path.join("\u0001")));
        const isCopying = copying?.from === name;
        // the default set shows every job; another set shows the jobs it changes, the others are added from its menu
        const shownJobs = isDefault ? jobs : jobs.filter((job) => readSetJobModel(overrides, job) !== "");
        const addableJobs = isDefault ? [] : jobs.filter((job) => readSetJobModel(overrides, job) === "");
        const overrideItems = addableJobs.map((job) => {
          const startWith = readDefaultChoice(job, drafts) || readDefaultJobModel(job)
            || listChoices("", null, job)[0]?.value || "";
          return { label: job.title, icon: "plus", isDisabled: startWith === "",
            hint: startWith === "" ? "no model can do this job yet" : `now the default set's: ${startWith}`,
            onSelect: () => writeSets({ ...sets, [name]: setLeaf(overrides, job.path, startWith) }) };
        });
        return (
          // every set starts closed, its name alone showing, like every other group of settings
          <details key={name} className="provider-set" aria-label={`provider set ${name}`} role="group">
            <summary className="provider-set-head">
              <span className="caret"><Icon name="chevron" size={13} /></span><h4>{name}</h4>
            </summary>
            <div className="provider-set-head">
              <div className="provider-set-actions">
                {chosenRow && (chosen === name
                  ? <span className="provider-set-chosen"><Icon name="check" size={14} /> Used by runs that pick no set</span>
                  : <button type="button" className="btn" aria-label={`use provider set ${name} for runs that pick no set`}
                    onClick={() => onEdit(chosenRow, name)}>Use for runs that pick no set</button>)}
                <button type="button" className="btn" onClick={() => setCopying({ from: name, name: "" })}
                  aria-label={`copy provider set ${name}`}><Icon name="copy" size={14} /> Copy</button>
                {!isDefault && (
                  <button type="button" className="btn danger" onClick={() => removeSet(name)}
                    aria-label={`remove provider set ${name}`}><Icon name="trash" size={14} /> Remove</button>
                )}
              </div>
            </div>
            {isCopying && (
              <div className="provider-set-add copy-form">
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
              {shownJobs.map((job) => {
                const defaultChoice = readDefaultChoice(job, drafts) || readDefaultJobModel(job);
                const current = isDefault ? readDefaultChoice(job, drafts) : readSetJobModel(overrides, job);
                const usual = typeof job.row.resolved === "string" ? job.row.resolved : "";
                const isProviderJob = job.key.startsWith("providers.");
                const builtIn = describeBuiltInTool(rows, job.key);
                const firstOption: SettingOption | null = isDefault
                  ? (job.key.startsWith("phaseModels.")
                    ? { value: "", label: usual ? describeModelOnProvider(rows, usual) : "no model chosen",
                      hint: "used when this job is given no model of its own", group: "" }
                    : isProviderJob ? { value: "", label: builtIn, hint: "runs on this computer, no model server", group: "" }
                      : null)
                  : { value: "", label: "Same as the default set",
                    hint: defaultChoice ? describeModelOnProvider(rows, defaultChoice) : isProviderJob ? builtIn : "no model chosen",
                    group: "" };
                const effective = current || defaultChoice;
                const choices = listChoices(current, firstOption, job);
                return (
                  <div key={job.key} className="job-link">
                    <span className="job-title">{job.title}</span>
                    {choices.length > 1 ? (
                      <OptionMenu name={`${name}: ${job.title}`} icon="layers" value={current} options={choices}
                        onChoose={(model) => {
                          if (isDefault) {
                            onEdit(job.row, model);
                            return;
                          }
                          writeSets({ ...sets, [name]: model === "" ? dropLeaf(overrides, job.path) : setLeaf(overrides, job.path, model) });
                        }} />
                    ) : (
                      // a job nothing else can do has no menu to open: it says what it runs on
                      <span className="job-fixed">{readOptionLabel(choices[0]?.label ?? "")}</span>
                    )}
                    <small className="muted job-runs">{describeJobLine(rows, effective, job.key,
                      readOptionLabel(choices.find((choice) => choice.value === current)?.label ?? choices[0]?.label ?? ""))}</small>
                  </div>
                );
              })}
            </div>
            {addableJobs.length > 0 && (
              <Menu label="Override a job" icon="plus" variant="ghost" items={overrideItems} />
            )}
            {!isDefault && (
              <>
                {shownJobs.length === 0 && otherLeaves.length === 0 && (
                  <p className="muted">It changes nothing yet: it runs exactly like the default set.</p>
                )}
                {otherLeaves.length > 0 && <h5 className="provider-set-subhead">Other values this set changes</h5>}
                {otherLeaves.map((leaf) => {
                  const fallback = findDefault(rows, leaf.path);
                  const key = leaf.path.join(".");
                  const label = leaf.path[0] === MODELS_KEY && leaf.path.length === 3
                    ? `${leaf.path[1]}: ${readSettingLabel(key)}` : readSettingLabel(key);
                  return (
                    <div key={key} className="provider-set-value">
                      <span className="job-title">{label}</span>
                      <input type="text" aria-label={`${name}: ${label}`} value={renderValue(leaf.value)}
                        onChange={(event) => writeSets({ ...sets, [name]: setLeaf(overrides, leaf.path,
                          parseTyped(event.target.value, fallback.found ? fallback.value : leaf.value)) })} />
                      <small className="muted">default: {fallback.found
                        ? describeValue(fallback.row, isEmptyValue(fallback.value) ? null : fallback.value)
                        : "not a known setting"}</small>
                      <button type="button" className="btn ghost" aria-label={`stop changing ${label} in ${name}`}
                        onClick={() => writeSets({ ...sets, [name]: dropLeaf(overrides, leaf.path) })}>Use default</button>
                    </div>
                  );
                })}
              </>
            )}
          </details>
        );
      })}
      <div className="provider-set-add">
        <input type="text" aria-label="new provider set name" placeholder="new set's name" value={newSet}
          onChange={(event) => setNewSet(event.target.value)} />
        <button type="button" className="btn" onClick={addSet} disabled={!newSetName || isNewNameTaken}>+ Add provider set</button>
        {newSetName && isNewNameTaken && <small className="error">a set has this name</small>}
      </div>
    </div>
  );
}
