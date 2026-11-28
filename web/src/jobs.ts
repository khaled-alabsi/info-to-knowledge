import { NO_SELECTION, sendJson } from "./api";
import type { Job, JobOnFailure, JobRequest, JobStatus, PhaseSelection } from "./api";
import { nameSpeedPreset, readSpeedSettings } from "./speed";

// What the Queue and a job's console both say of a job: its choices, its name, what its status means, and the two
// things done to it (taken off the queue, interrupted).

// The ended jobs that can be queued again with the same choices: none of them ran to an end of its own
export const REQUEUABLE_STATUSES: JobStatus[] = ["cancelled", "lost", "crashed"];

// A run with a good document shows its last job's state in words of its own (the owner: "show only last state + suggest
// new state for element has at least one succsfule run"): a change that failed left "done" on the row, the document
// still being there. A plain run still under way past its document is no change, so it reads running.
const CHANGE_WORDS: Partial<Record<JobStatus, { label: string; pillClass: string }>> = {
  queued: { label: "change queued", pillClass: "queued" },
  running: { label: "changing", pillClass: "running" },
  retrying: { label: "change retrying", pillClass: "retrying" },
  blocked: { label: "change failed, queue held", pillClass: "failed" },
  incomplete: { label: "change failed", pillClass: "failed" },
  crashed: { label: "change failed", pillClass: "failed" },
  refused: { label: "change failed", pillClass: "failed" },
  stopped: { label: "change stopped", pillClass: "stopped" },
};

// A change whose job ended with no server watching (`lost`) has no end of its own: the run's own record says whether it
// finished, as the run's header reads it (a knowledge pass stopped at part 16 of 21 read "done" in the list)
export function describeChangePill(job: Job | undefined, hasDocument: boolean,
  runStatus = ""): { label: string; pillClass: string } | null {
  if (!job || !hasDocument) return null;
  const request = job.request;
  const isChange = request.only_phase !== null || request.from_phase !== null || Boolean(request.document_pass);
  if ((job.status === "queued" || job.status === "running" || job.status === "retrying") && !isChange) return null;
  if (job.status === "lost" && isChange && runStatus && runStatus !== "complete" && runStatus !== "running") {
    return CHANGE_WORDS.incomplete ?? null;
  }

  return CHANGE_WORDS[job.status] ?? null;
}

export function describeJob(job: Job): string {
  const request = job.request;
  if (request.purpose) return request.purpose;
  const parts = [];
  if (request.is_resuming) parts.push("resume");
  if (request.only_phase !== null) parts.push(`phase ${request.only_phase}`);
  if (request.from_phase !== null) parts.push(`from ${request.from_phase}`);
  if (request.to_phase !== null) parts.push(`to ${request.to_phase}`);
  if (request.worker_indexes.length) parts.push(`frames ${request.worker_indexes.join(", ")}`);
  if (request.is_dry_run) parts.push("dry run");
  // the choices a job runs with beside its phases (the owner, queuing 32 videos fast: each read "every phase" alone)
  const choices = [request.profile ? `profile ${request.profile}` : "",
    request.workflow === "fast-processing" || request.workflow === "direct-draft" ? "Fast" : request.workflow === "quality" ? "Quality" : "",
    request.provider_set ? `set ${request.provider_set}` : "",
    request.speed_preset ? `${nameSpeedPreset(request.speed_preset)}${request.speed_settings?.length ? " (changed)" : ""}` : ""]
    .filter(Boolean);
  return [parts.join(" · ") || "every phase", ...choices].join(" · ");
}

// What a finished job's status means, for its pill's title; the exit code where there is one
export function describeJobStatus(job: Job): string | undefined {
  const code = job.exit_code !== null ? ` (exit code ${job.exit_code})` : "";
  if (job.status === "lost") return "the server restarted while it ran, so its end is unknown: open its run to see how far it got";
  if (job.status === "crashed") return `the process was killed${code}: open its console for the last lines`;
  if (job.status === "incomplete") return `the run ended before its last phase, on a blocked step or a failure${code}: open its run to see why, then Resume`;
  if (job.status === "refused") return `the run line was refused before any phase ran${code}: its console says why`;
  if (job.status === "stopped") return `stopped on request, by Stop or Interrupt${code}: Resume goes on from where it stopped`;
  if (job.status === "cancelled") return "taken off the queue before it ran";
  if (job.status === "retrying") return `it failed${code} and runs again with --resume before any other job`;
  if (job.status === "blocked") return `it failed on its last try${code}, and the queue holds until you retry or skip it`;
  return job.exit_code !== null ? `exit code ${job.exit_code}` : undefined;
}

// Which retry a job is on, for its row and its detail view: "retry 1 of 2 at 10:42:05" while it waits, "retry 1 of 2"
// while that retry runs, "failed 3 tries" once it holds the queue; "" on a first try or a record older than retries
export function describeRetry(job: Job): string {
  const attempt = job.attempt ?? 0;
  const retries = (job.tries ?? 1) - 1;
  if (job.status === "retrying") return `retry ${attempt} of ${retries} at ${(job.retry_at ?? "").slice(11)}`;
  if (job.status === "running" && attempt > 1) return `retry ${attempt - 1} of ${retries}`;
  if (job.status === "blocked") return `failed ${attempt} ${attempt === 1 ? "try" : "tries"}`;
  return "";
}

// The jobs whose run may still go on: one running, waiting, waiting for its retry, or holding the queue
export function isJobOpen(job: Job): boolean {
  return ["queued", "running", "retrying", "blocked"].includes(job.status);
}

// Sets what the queue does after this job's last failed try; the answer is the line the page's message shows
export async function setJobOnFailure(job: Job, choice: JobOnFailure): Promise<string> {
  await sendJson<Job>(`/jobs/${job.job_id}/on-failure`, { choice });
  return `Job ${job.job_id}: on failure, ${choice === "block" ? "the queue holds" : "the queue goes on"}`;
}

// A job is named by the run folder it writes; the recording is named beside it only when the folder is named otherwise,
// as a converted run is (the owner read "Track 14.wav" in the queue as Track 14 run again)
export function nameJob(job: Job): { folder: string; recording: string } {
  const recording = job.request.video.split("/").pop() ?? "";
  const folder = job.request.output_root.split("/").pop() || recording;
  return { folder, recording: folder === recording.replace(/\.[^.]+$/, "") ? "" : recording };
}

// The choices a job was queued with, as a new request takes them: every field of its record, its own settings profile
// (never the server's, which its run line names when it had none), and its phase models from `PHASE=NAME` pairs
export function readJobSelection(request: JobRequest): PhaseSelection {
  return {
    ...NO_SELECTION,
    only_phase: request.only_phase, from_phase: request.from_phase, to_phase: request.to_phase,
    worker_indexes: request.worker_indexes, is_resuming: request.is_resuming, is_dry_run: request.is_dry_run,
    profile: request.profile ?? "", tags: request.tags ?? [], ingesting_profile: request.ingesting_profile ?? "",
    is_run_as_audio: request.is_run_as_audio ?? false, workflow: request.workflow ?? "",
    provider_set: request.provider_set ?? "",
    speed_preset: request.speed_preset ?? "", speed_settings: readSpeedSettings(request.speed_settings ?? []),
    pdf_mode: request.pdf_mode ?? "", pdf_ocr_engine: request.pdf_ocr_engine ?? "",
    document_pass: request.document_pass ?? "", document_wishes: request.document_wishes ?? "",
    document_source: request.document_source ?? "",
    knowledge_filter_model: request.knowledge_filter_model ?? "",
    range_start: request.range_start ?? "", range_end: request.range_end ?? "", range_pages: request.range_pages ?? "",
    phase_models: Object.fromEntries((request.phase_models ?? []).map((pair) => {
      const at = pair.indexOf("=");
      return [pair.slice(0, at), pair.slice(at + 1)];
    })),
  };
}

// What queueing did, in one wording for every page that queues: the queue runs one job at a time, so with nothing ahead
// only the first starts now and the others wait behind it; with jobs ahead, every one waits behind them
// More names than this are counted, never listed: 32 queued at once listed every name and the message covered half
// the page
const LISTED_NAMES = 3;

function listNames(names: string[]): string {
  return names.length > LISTED_NAMES ? `${names.length} recordings` : names.join(", ");
}

export function describeQueued(names: string[], jobsAhead: number, startedWords = "it shows as starting until its run appears"): string {
  if (jobsAhead) return `Queued ${listNames(names)} behind ${jobsAhead} job${jobsAhead === 1 ? "" : "s"}`;
  const [first, ...rest] = names;
  return `Starting ${first}: ${startedWords}${rest.length ? `; ${listNames(rest)} queued behind it` : ""}`;
}

// The jobs a new one waits behind: the one running, the ones queued, one waiting for its retry and one holding the queue
export function countJobsAhead(jobs: Job[]): number {
  return jobs.filter(isJobOpen).length;
}

// What each action did to a job, as the page's message says it
const ACTION_WORDS = {
  cancel: "taken off the queue",
  interrupt: "interrupted",
  retry: "runs again before any other job",
  skip: "skipped, the next job starts",
};

// Takes a waiting job off the queue, interrupts a running one, or retries or skips one holding the queue; the answer is
// the line the page's message shows
export async function sendJobAction(job: Job, action: keyof typeof ACTION_WORDS): Promise<string> {
  await sendJson<Job>(`/jobs/${job.job_id}/${action}`);
  return `Job ${job.job_id}: ${ACTION_WORDS[action]}`;
}
