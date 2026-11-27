import { formatDuration } from "../api";
import type { PassPart, RunDetail, StageProgress } from "../api";

// What the run page's progress card shows of one phase, worked out from the run's state alone.

// Phases 1 and 2's stages by the name the run records, as the page names them
export const STAGE_TITLES: Record<string, string> = { rules: "rules", classifier: "classifier", model: "redundancy model",
  similarity: "similarity recheck", transcription: "transcription", speakers: "speakers",
  // a PDF's phase 1, step by step
  "text layer": "reading each page's text layer", figures: "finding figures", parsing: "parsing pages",
  markdown: "writing markdown", "scanned pages": "reading scanned pages", "page cleaning": "cleaning pages" };

// A running stage that counted nothing for this long is shown as stalled: a page takes a few seconds, a scanned page
// 8 to 20, so ten minutes with no page counted means it is stuck in a step that counts nothing, or stopped
export const STALLED_SECONDS = 10 * 60;

// What phase 3 reads one at a time: a video's frames, a PDF's pages, or read as text only its figures (the owner:
// "what is frames in pdf?")
export function describeUnit(pdfMode: string): { one: string; many: string } {
  if (pdfMode === "text-only") return { one: "figure", many: "Figures" };
  if (pdfMode === "pages-as-images") return { one: "page", many: "Pages" };
  return { one: "frame", many: "Frames" };
}

// The bar's segments: done first, waiting last, so a bar fills from the left as its phase works; a status `order` does
// not name goes after the ones it does
export function sortCounts(counts: Record<string, number>, order: string[]): [string, number][] {
  const rank = (name: string) => (order.includes(name) ? order.indexOf(name) : order.length);
  return Object.entries(counts).filter(([, count]) => count > 0).sort(([one], [other]) => rank(one) - rank(other));
}

// A time per item; under 10 s it keeps its hundredths: the rules sample a second of video in a fraction of one
export function formatPace(seconds: number): string {
  return seconds < 10 ? `${seconds.toFixed(2)} s` : formatDuration(seconds);
}

// A stage row's frames: what it removed (from the dropped frames themselves, so a run whose record kept no counts shows
// them too; the rules take frames and remove none) and what it left, null where the record does not say
export function countStageFrames(stage: StageProgress): { removedCount: number | null; left: number | null } {
  const isFilter = stage.unit === "frames";
  let removedCount: number | null = null;
  if (stage.removed) removedCount = stage.removed.length;
  else if (isFilter && stage.kept != null && stage.total != null) removedCount = stage.total - stage.kept;
  if (!isFilter) return { removedCount, left: stage.frames };
  if (stage.kept != null) return { removedCount, left: stage.kept };
  if (stage.status === "done" && stage.total != null && removedCount != null) return { removedCount, left: stage.total - removedCount };
  return { removedCount, left: null };
}

export interface RunProgress {
  title: string;
  detail: string;
  pace: string;
  counts: [string, number][];
  note: string;
  // when the running stage last counted one more ("Last progress 3 min ago"), and whether that is so long it is stalled
  lastProgress?: string;
  isStalled?: boolean;
}

// The stages phase 1 or 2 lists under the bar, for the phase whose bar it is; other phases list none here
export function listStages(detail: RunDetail | undefined, phaseNumber: number | undefined): StageProgress[] {
  if (phaseNumber === 1) return detail?.transcribing ?? [];
  if (phaseNumber === 2) return detail?.frames_preparing ?? [];
  return [];
}

// Phase 4's stages, the stage it is in (the first with a step not accepted: the table of contents, then the chapters,
// then the final summary), and whether every stage is done
export function findDocumentStage(detail: RunDetail | undefined) {
  // A markdown conversion writes its document with no model call, so it has no steps, whatever the server lists
  const stages = detail?.pdf_mode === "markdown-conversion" ? [] : detail?.document_stages ?? [];
  const openAt = stages.findIndex((stage) => Object.entries(stage.counts).some(([status, count]) => status !== "accepted" && count > 0));
  const at = Math.max(0, openAt);
  return { stages, at, stage: stages[at], isWritten: stages.length > 0 && openAt === -1 };
}

// The bar of one phase: its title, where it is, its pace and its counts, or null for a phase that records nothing to
// count. A finished phase shows its outcome, never its first stage again.
export function chooseProgress(detail: RunDetail, phaseNumber: number | undefined): RunProgress | null {
  // 1. Phase 1: its stages, the transcription and then the speakers
  const transcribing = detail.transcribing ?? [];
  if (phaseNumber === 1 && transcribing.length > 0) {
    const doneCount = transcribing.filter((stage) => stage.status === "done").length;
    const at = Math.max(0, transcribing.findIndex((stage) => stage.status !== "done"));
    const stage = transcribing[at];
    const where = doneCount === transcribing.length ? `${transcribing.length} stages done`
      : `${STAGE_TITLES[stage.name] ?? stage.name} · stage ${at + 1} of ${transcribing.length}`;
    // A stage that counts what it does (a PDF's scanned pages, 8 to 20 s each) shows that count and its pace: counted
    // by stages alone, the bar waited minutes with nothing moving
    const total = stage.status === "running" ? Math.round(stage.total ?? 0) : 0;
    if (total > 0) {
      const read = Math.min(total, Math.round(stage.done));
      const left = stage.seconds_left != null ? ` · about ${formatDuration(stage.seconds_left)} left` : "";
      const since = stage.seconds_since_progress;
      return { title: "Transcribing", detail: `${where} · ${read.toLocaleString("en")} of ${total.toLocaleString("en")} ${stage.unit}`,
        pace: read > 0 ? `One page takes ${formatPace(stage.seconds / read)} on average${left}` : "", note: "",
        counts: sortCounts({ done: read, waiting: total - read }, ["done", "waiting"]),
        lastProgress: since != null ? `Last progress ${since < 60 ? "under a minute" : formatDuration(since)} ago` : "",
        isStalled: since != null && since >= STALLED_SECONDS };
    }
    return { title: "Transcribing", detail: where, pace: "", note: "",
      counts: sortCounts({ done: doneCount, waiting: transcribing.length - doneCount }, ["done", "waiting"]) };
  }

  // 2. Phase 2: what it kept and dropped of the frames its rules took once it is done, else the stage it is on
  const preparing = detail.frames_preparing ?? [];
  if (phaseNumber === 2 && preparing.length > 0) {
    const framesTaken = preparing[0]?.frames ?? 0;
    if (preparing.every((stage) => stage.status === "done") && framesTaken > 0) {
      const framesKept = Object.values(detail.frames).reduce((sum, count) => sum + count, 0);
      return { title: "Preparing frames", detail: `${preparing.length} stages done · frames the rules took`, pace: "", note: "",
        counts: sortCounts({ kept: framesKept, dropped: Math.max(0, framesTaken - framesKept) }, ["kept", "dropped"]) };
    }
    const at = Math.max(0, preparing.findIndex((stage) => stage.status !== "done"));
    const stage = preparing[at];
    const total = Math.round(stage.total ?? 0);
    const doneCount = Math.min(total, Math.round(stage.done));
    // the classifier counts its frames only once it ends, so it has no pace while it runs
    const hasPace = stage.status === "running" && doneCount > 0 && stage.name !== "classifier";
    return {
      title: "Preparing frames",
      detail: `${STAGE_TITLES[stage.name] ?? stage.name} · stage ${at + 1} of ${preparing.length} · ${stage.unit}`,
      pace: hasPace ? `One ${stage.name === "rules" ? "second of video" : "frame"} takes ${formatPace(stage.seconds / doneCount)} on average` : "",
      counts: sortCounts({ done: doneCount, waiting: total - doneCount }, ["done", "waiting"]),
      note: stage.name === "classifier" && stage.status === "running" ? "the classifier counts its frames only once it ends" : "",
    };
  }

  // 3. Phase 3: an audio recording's preparation steps (the first not done), else the frames by status
  const preparation = detail.preparation ?? [];
  if (phaseNumber === 3 && preparation.length > 0) {
    const at = Math.max(0, preparation.findIndex((step) => step.total === null || step.done < step.total));
    const step = preparation[at];
    return { title: "Preparing the notes", detail: `${step.title} · step ${at + 1} of ${preparation.length}`, pace: "",
      counts: sortCounts({ accepted: step.done, waiting: Math.max(0, (step.total ?? 0) - step.done) }, ["accepted", "waiting"]),
      note: step.total === null ? "its items are counted once the step before it is accepted" : "" };
  }
  if (phaseNumber === 3) {
    const unit = describeUnit(detail.pdf_mode ?? "");
    return { title: "Frames", detail: "", note: "",
      pace: detail.seconds_per_frame > 0 ? `One ${unit.one} takes ${formatDuration(detail.seconds_per_frame)} on average` : "",
      counts: sortCounts(detail.frames, ["written", "skipped", "analyzed", "blocked", "pending"]) };
  }

  // 4. Phase 4: the whole phase once every stage is done, else the stage it is in
  const document = findDocumentStage(detail);
  if (phaseNumber === 4 && document.isWritten) {
    const writtenSteps = document.stages.reduce((sum, stage) => sum + (stage.counts.accepted ?? 0), 0);
    return { title: "Document written", detail: `${document.stages.length} stages done`, pace: "", note: "",
      counts: sortCounts({ accepted: writtenSteps }, ["accepted"]) };
  }
  if (phaseNumber === 4 && document.stage) {
    return { title: `Writing the ${document.stage.name}`, detail: `stage ${document.at + 1} of ${document.stages.length}`, pace: "",
      counts: sortCounts(document.stage.counts, ["accepted", "running", "blocked", "waiting"]),
      note: !detail.is_document_total_known && document.at === 0 ? "the chapters' headings are counted once the agenda is accepted" : "" };
  }

  // 5. Phase 7: the pass's parts, done, running and waiting, with the time a finished part took and the time left
  const parts = detail.document_pass_parts ?? [];
  if (phaseNumber === 7 && parts.length > 0) {
    return describePassProgress(parts, detail.document_pass ?? "");
  }

  // 6. Every other phase records no steps to count
  return null;
}

// What the bar says while a pass works its parts, by the pass's word ("Reshaping knowledge"); the time left is the
// finished parts' average times the parts not finished, the running one counted from what it has spent
export function describePassProgress(parts: PassPart[], pass: string): RunProgress {
  const title = PASS_PROGRESS_TITLES[pass] ?? "Changing the document";
  const counts: Record<string, number> = {};
  for (const part of parts) counts[part.status] = (counts[part.status] ?? 0) + 1;
  const done = parts.filter((part) => part.status === "done");
  const open = parts.find((part) => part.status === "running" || part.status === "stopped");
  const where = done.length === parts.length ? `${parts.length} parts done`
    : open ? `part ${open.number} of ${parts.length}${open.status === "stopped" ? " (stopped)" : ""}` : `${parts.length} parts`;
  let pace = "";
  if (done.length > 0) {
    const average = done.reduce((sum, part) => sum + part.seconds, 0) / done.length;
    const leftSeconds = parts.filter((part) => part.status !== "done")
      .reduce((sum, part) => sum + Math.max(0, average - (part.status === "running" ? part.seconds : 0)), 0);
    pace = `One part takes ${formatDuration(average)} on average${leftSeconds > 0 ? ` · about ${formatDuration(leftSeconds)} left` : ""}`;
  } else if (open && open.status === "running") {
    pace = `part ${open.number} running for ${formatDuration(open.seconds)} · ${open.calls} model call${open.calls === 1 ? "" : "s"}`;
  }
  // a part waiting for a model host with no working model says so above every other pace: its time is no work done
  const waiting = parts.find((part) => part.status === "running" && part.host_wait);
  if (waiting) {
    pace = `part ${waiting.number} waits for the model host for ${formatDuration(waiting.host_wait_seconds ?? 0)}: ${waiting.host_wait}`;
  }
  return { title, detail: where, pace, note: done.length === 0 ? "the time left is known once the first part ends" : "",
    counts: sortCounts(counts, ["done", "running", "stopped", "waiting"]) };
}

const PASS_PROGRESS_TITLES: Record<string, string> = { knowledge: "Reshaping knowledge", cleanup: "Cleaning up",
  rewrite: "Rewriting" };
