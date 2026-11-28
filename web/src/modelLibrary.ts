import type { SettingRow } from "./api";
import { describeHost, isOwnerChange } from "./settingRunValue";
import { PHASE_LABELS, readSettingLabel } from "./settingsLabels";

// The models library and the jobs that link to it, read from the rows the server lists. A model is every `models.<name>.*`
// row; a job is something a run asks a model to do: each phase that calls a model (`phaseModels.<phase>`) and each job a
// provider does (`providers.<kind>.libraryModel`).

export const MODELS_KEY = "models";
export const PHASE_MODELS_KEY = "phaseModels";
export const SETS_KEY = "providerSets";
export const DEFAULT_SET = "default";

export type Tree = Record<string, unknown>;

export function isTree(value: unknown): value is Tree {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function asTree(value: unknown): Tree {
  return isTree(value) ? value : {};
}

export function readPath(row: SettingRow): string[] {
  return row.path ?? row.key.split(".");
}

// The models' names in the order the rows give them
export function listModelNames(rows: SettingRow[]): string[] {
  return [...new Set(rows.filter((row) => readPath(row)[0] === MODELS_KEY && readPath(row).length === 3).map((row) => readPath(row)[1]))];
}

// The rows of one model, its field rows by the field's name
export function readModelRows(rows: SettingRow[], name: string): Record<string, SettingRow> {
  const found: Record<string, SettingRow> = {};
  for (const row of rows) {
    const path = readPath(row);
    if (path[0] === MODELS_KEY && path.length === 3 && path[1] === name) found[path[2]] = row;
  }
  return found;
}

// A model the owner's file or a profile added: no shipped setting holds it, so it can be taken out. A model of the shipped
// settings can be changed, never removed
export function isModelRemovable(rows: SettingRow[], name: string): boolean {
  const own = Object.values(readModelRows(rows, name));
  return own.length > 0 && !own.some((row) => row.source === "default") && own.some((row) => isOwnerChange(row.source));
}

function readFieldValue(fields: Record<string, SettingRow>, field: string, drafted: Tree | undefined): unknown {
  if (drafted && field in drafted) return drafted[field];
  return fields[field]?.value;
}

// What a model runs on, as a person says it: the provider's name, the model's name there and how much text it reads at once. `drafted` holds the fields of a model not saved yet
export function describeModelRun(rows: SettingRow[], name: string, drafted?: Tree): { host: string; model: string; context: string } {
  const fields = readModelRows(rows, name);
  const spec = String(readFieldValue(fields, "spec", drafted) ?? "");
  const provider = spec.split(":")[0];
  const hostValue = readFieldValue(fields, "host", drafted);
  const hosts = asTree(rows.find((row) => row.key === "hosts")?.value);
  const hostName = typeof hostValue === "string" && hostValue !== "" ? hostValue : provider in hosts ? provider : "";
  const context = readFieldValue(fields, "numCtx", drafted);
  return {
    host: hostName ? describeHost(hostName)
      : provider === "openai" ? "OpenAI's own service" : "no server chosen",
    model: spec.includes(":") ? spec.slice(spec.indexOf(":") + 1) : spec || "automatic",
    context: typeof context === "number" ? describePages(context) : "automatic",
  };
}

// A size in tokens as a person pictures it: a token is about three quarters of a word and a page about 500 words
export function describePages(tokens: number): string {
  const pages = Math.max(1, Math.round((tokens * 0.75) / 500));
  return `about ${pages.toLocaleString("en-US")} ${pages === 1 ? "page" : "pages"}`;
}

// A wait in seconds as a person says it: "1 hour", "30 minutes", "90 seconds"
export function describeWait(seconds: number): string {
  const unit = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;
  if (seconds >= 3600 && seconds % 3600 === 0) return unit(seconds / 3600, "hour");
  if (seconds >= 60 && seconds % 60 === 0) return unit(seconds / 60, "minute");
  return unit(seconds, "second");
}

// How long the program waits for one reply of a model, in words; empty when the model sets no wait of its own
export function describeModelWait(rows: SettingRow[], name: string): string {
  const wait = readModelRows(rows, name).requestTimeoutS?.value;
  return typeof wait === "number" && wait > 0 ? `waits up to ${describeWait(wait)} for a reply` : "";
}

// The two values that tell apart models running the same model on the same server: how long a reply may be and the
// repeat penalty; empty when the model sets neither
export function describeModelReplies(rows: SettingRow[], name: string, drafted?: Tree): string {
  const fields = readModelRows(rows, name);
  const reply = readFieldValue(fields, "maxTokens", drafted);
  const penalty = asTree(readFieldValue(fields, "sampling", drafted)).presence_penalty;
  const parts = [typeof reply === "number" ? `writes up to ${describePages(reply)}` : "",
    typeof penalty === "number" ? `repeat penalty ${penalty.toFixed(1)}` : ""];
  return parts.filter((part) => part !== "").join(" · ");
}

// `kind` is the job a provider must be able to do for the job's model (a provider's `kinds`): chat for the phases, the kind
// itself for a speech, speaker or picture job. `suits` are the kinds of work the job needs from a chat model (a model's own
// `suits`); empty when any model of the provider will do
export type Job = { key: string; path: string[]; title: string; row: SettingRow; kind: string; suits: string[] };

const PROVIDER_JOB = /^providers\.([a-z]+)\.libraryModel$/;

// What each phase needs from the model it calls: frames processing reads pictures; frames preparing filters frames (or, for
// an audio recording, cleans up the transcript); the others write the document
const PHASE_SUITS: Record<string, string[]> = {
  frames_preparing: ["filtering", "writing"],
  frames_processing: ["frames"],
};
const WRITING_SUITS = ["writing"];
// The scanned page reader looks at pictures of pages
const PROVIDER_SUITS: Record<string, string[]> = { chat: ["frames"] };

// The jobs, in the order the rows give them: each phase that calls a model, then each job a provider does (scanned page
// reader, speech to text, speaker detection, ...). The fast workflow has no jobs of its own: it uses the set's
export function listJobs(rows: SettingRow[]): Job[] {
  const phases = rows.filter((row) => row.key.startsWith(`${PHASE_MODELS_KEY}.`));
  const providers = rows.filter((row) => PROVIDER_JOB.test(row.key));
  return [...phases, ...providers].map((row) => {
    const providerKind = PROVIDER_JOB.exec(row.key)?.[1];
    const phase = row.key.slice(PHASE_MODELS_KEY.length + 1);
    const title = providerKind ? readSettingLabel(row.key) : PHASE_LABELS[phase] ?? readSettingLabel(row.key);
    const suits = providerKind ? PROVIDER_SUITS[providerKind] ?? [] : PHASE_SUITS[phase] ?? WRITING_SUITS;
    return { key: row.key, path: readPath(row), row, title, kind: providerKind ?? "chat", suits };
  });
}

// The name of the provider a model runs on: its own `host`, else the provider named like the start of its spec; "" when none
export function readModelProvider(rows: SettingRow[], name: string): string {
  const fields = readModelRows(rows, name);
  const hostValue = fields.host?.value;
  if (typeof hostValue === "string" && hostValue !== "") return hostValue;
  const start = String(fields.spec?.value ?? "").split(":")[0];
  return start in asTree(rows.find((row) => row.key === "hosts")?.value) ? start : "";
}

// The jobs a provider can do: the `kinds` it lists, or chat when it lists none
export function readProviderKinds(rows: SettingRow[], provider: string): string[] {
  const kinds = asTree(asTree(rows.find((row) => row.key === "hosts")?.value)[provider]).kinds;
  return Array.isArray(kinds) && kinds.length > 0 ? kinds.map(String) : ["chat"];
}

// The kinds of work a model says it suits (`frames`, `writing`, `filtering`); empty when it names none and so suits every job
export function readModelSuits(rows: SettingRow[], name: string): string[] {
  const suits = readModelRows(rows, name).suits?.value;
  return Array.isArray(suits) ? suits.map(String) : [];
}

// Whether a model can do a job: the provider it runs on lists the job's kind, and the model suits the job's kind of work
// (a model that names no suits suits any). A model on no listed provider (OpenAI's own service, or one the page cannot
// place) is offered for chat only
export function canModelDo(rows: SettingRow[], name: string, job: Pick<Job, "kind" | "suits">): boolean {
  const provider = readModelProvider(rows, name);
  if (!(provider ? readProviderKinds(rows, provider).includes(job.kind) : job.kind === "chat")) return false;
  const suits = readModelSuits(rows, name);
  return suits.length === 0 || job.suits.length === 0 || suits.some((suit) => job.suits.includes(suit));
}

// The model a job calls in the default set: the row's own value, or the model the phase calls when it names none
export function readDefaultJobModel(job: Job): string {
  const own = job.row.value;
  if (typeof own === "string" && own !== "") return own;
  const resolved = job.row.resolved;
  return typeof resolved === "string" ? resolved : "";
}

// The model a job calls in a provider set: what the set names, or "" when the set leaves it to the default set
export function readSetJobModel(set: Tree, job: Job): string {
  let node: unknown = set;
  for (const part of job.path) node = isTree(node) ? node[part] : undefined;
  return typeof node === "string" ? node : "";
}

export type ModelUse = { set: string; jobs: string[] };

// The provider sets that call a model, each with the jobs it calls it for: "default" first when its jobs do, then each set
// naming the model in a job of its own
export function listModelUses(rows: SettingRow[], sets: Tree, name: string): ModelUse[] {
  const jobs = listJobs(rows);
  const uses: ModelUse[] = [];
  const defaultJobs = jobs.filter((job) => readDefaultJobModel(job) === name).map((job) => job.title);
  if (defaultJobs.length > 0) uses.push({ set: DEFAULT_SET, jobs: defaultJobs });
  for (const [setName, set] of Object.entries(sets)) {
    const own = jobs.filter((job) => readSetJobModel(asTree(set), job) === name).map((job) => job.title);
    if (own.length > 0) uses.push({ set: setName, jobs: own });
  }
  return uses;
}

// The names of the provider sets that call a model
export function listModelUsers(rows: SettingRow[], sets: Tree, name: string): string[] {
  return listModelUses(rows, sets, name).map((use) => use.set);
}

// "default (Frames processing), Office (Review, Document writing)": where a model is used, as the card says it
export function describeModelUses(uses: ModelUse[]): string {
  return uses.map((use) => `${use.set} (${use.jobs.join(", ")})`).join(", ");
}
