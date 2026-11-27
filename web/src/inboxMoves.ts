import { sendJson } from "./api";

// The two places an inbox row is moved aside to, and back from: the server serves both through one request each.
export type InboxPlace = "archive" | "trash";

export interface MoveOutcome {
  video: string;
  moved_as: string;
  error: string;
}

export interface MoveBackOutcome {
  name: string;
  restored_as: string;
  error: string;
}

// Moves the videos (their recordings only) and the run folders no inbox video owns, whole, into the place.
export function moveRows(place: InboxPlace, videos: string[], runFolders: string[]): Promise<MoveOutcome[]> {
  return sendJson<MoveOutcome[]>(`/inbox/${place}`, { videos, run_folders: runFolders });
}

// Moves one entry of the place back where it came from, under the name it had.
export async function moveBack(place: InboxPlace, name: string): Promise<MoveBackOutcome> {
  const [outcome] = await sendJson<MoveBackOutcome[]>(`/inbox/${place}/restore`, { names: [name] });
  return outcome;
}

// The outcome of moving rows, in one line: every refusal, else what moved.
export function describeMoveOutcomes(outcomes: MoveOutcome[], movedWords: string): { text: string; isError: boolean } {
  const failed = outcomes.filter((outcome) => outcome.error);
  return failed.length ? { text: failed.map((outcome) => outcome.error).join("; "), isError: true }
    : { text: movedWords, isError: false };
}
