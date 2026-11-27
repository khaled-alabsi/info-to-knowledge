import { fetchJson, putJson, sendJson } from "./api";

// The workers' instructions as the page edits them: the tree of workflows, phases and steps,
// one file with its default and the text in force, its versions, and the full prompt a step's worker gets.

export interface InstructionStep {
  key: string;
  title: string;
  who: string;
  variants: string[];
  files: string[];
}

export interface InstructionPhase {
  number: number;
  title: string;
  steps: InstructionStep[];
}

export interface InstructionWorkflow {
  key: string;
  title: string;
  variants: { key: string; title: string }[];
  phases: InstructionPhase[];
}

export type InstructionFileKind = "instruction" | "shared" | "document" | "compaction";

export interface InstructionFileEntry {
  id: string;
  title: string;
  kind: InstructionFileKind;
  used_by: { workflow: string; step: string }[];
  is_edited: boolean;
  is_default_changed: boolean;
}

export interface InstructionCatalogue {
  workflows: InstructionWorkflow[];
  files: InstructionFileEntry[];
}

export interface InstructionFile extends InstructionFileEntry {
  default: string;
  text: string;
  default_at_edit: string;
  versions: number;
}

export interface InstructionVersion {
  at: string;
  kind: "edit" | "restore" | "reset";
  account: string;
  note: string;
  size_bytes: number;
}

export interface InstructionVersionText {
  at: string;
  kind: InstructionVersion["kind"];
  account: string;
  note: string;
  text: string;
  default_text: string;
}

export interface InstructionSaved {
  is_saved: boolean;
  problems: string[];
  at?: string;
  is_changed?: boolean;
}

const query = (id: string) => `id=${encodeURIComponent(id)}`;

export const fetchInstructionCatalogue = () => fetchJson<InstructionCatalogue>("/instructions");

export const fetchInstructionFile = (id: string) => fetchJson<InstructionFile>(`/instructions/file?${query(id)}`);

export const fetchInstructionVersions = (id: string) =>
  fetchJson<InstructionVersion[]>(`/instructions/versions?${query(id)}`);

export const fetchInstructionVersion = (id: string, at: string) =>
  fetchJson<InstructionVersionText>(`/instructions/version?${query(id)}&at=${encodeURIComponent(at)}`);

export const saveInstruction = (id: string, text: string, note: string, kind: "edit" | "restore" = "edit") =>
  putJson<InstructionSaved>("/instructions/file", { id, text, note, kind });

export const resetInstruction = (id: string) =>
  sendJson<{ at: string; is_changed: boolean }>("/instructions/reset", { id });

export const fetchInstructionPreview = (step: string, workflow: string, id = "", text = "") =>
  sendJson<{ text: string; error: string }>("/instructions/preview", { step, workflow, id, text });
