import type { SettingRow } from "./api";

// What the Settings page says for a setting with no value of its own: the value a run uses (the server's `resolved`)
// with where it comes from (`resolved_note`), never an empty box or "(not set)"

type RunValueFields = Pick<SettingRow, "kind"> & Partial<Pick<SettingRow, "resolved" | "resolved_note">>;

export function isEmptyValue(value: unknown): boolean {
  return value === null || value === undefined || value === "";
}

// "lmstudio (from the model's provider)", or only the note when a run uses no value ("found from the recording"); a
// row the server knows nothing to say for reads "automatic", or "not used" for a text box of extra wishes
export function describeRunValue(row: RunValueFields): string {
  const { resolved, resolved_note: note } = row;
  if (!isEmptyValue(resolved)) return note ? `${String(resolved)} (${note})` : String(resolved);
  if (note) return note;
  return row.kind === "long-text" ? "not used" : "automatic";
}

// A value as the page prints it: its own text, or what a run uses when it has none
export function describeValue(row: RunValueFields | null, value: unknown): string {
  if (!isEmptyValue(value)) return typeof value === "string" ? value : JSON.stringify(value);
  return row ? describeRunValue(row) : "automatic";
}

// The label of an empty entry in a list of choices, which the server words from what a run uses when it can
export function readOptionLabel(label: string): string {
  return label === "" || label === "(empty)" ? "automatic" : label;
}

// A value an owner's own file or a provider set sets, against the program's and the shipped default settings' values
export function isOwnerChange(source: string): boolean {
  return source !== "code" && source !== "default";
}

// A provider as a person says it: its name, never its network address; a short name such as "gpu" is an abbreviation, so "GPU"
export function describeHost(name: string): string {
  return name.length <= 3 ? name.toUpperCase() : name;
}
