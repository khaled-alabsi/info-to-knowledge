// Invented answers of `i2k serve` for the end-to-end tests: three inbox recordings, three run folders, two jobs, the
// phases, the settings and the setup check. Every name is invented (AcmeSync, Jonas, Gateway); no real meeting.
import type {
  AccountRow, AuthState, CompressibleVideo, DownloadPart, InboxItem, Job, KnownTags, Phase, PhaseModels, Project, ProjectsState, RunDetail,
  RunSummary, SettingRow, SetupCheck,
} from "../../src/api";
import { SPEED_OPTIONS } from "../../src/test/speedOptions";

export const PHASES: Phase[] = [
  { number: 1, name: "transcribe", summary: "Turns the speech into a timed transcript.", reads: "the recording",
    writes: "01_transcript.json", is_model_used: false, is_on: true },
  { number: 2, name: "frames_preparing", summary: "Takes the frames of the shared screen.", reads: "the recording",
    writes: "02_frames.json", is_model_used: true, is_on: true },
  { number: 3, name: "frames_processing", summary: "Writes each frame's notes.", reads: "02_frames.json",
    writes: "03_notes.md", is_model_used: true, is_on: true },
  { number: 4, name: "finalize", title: "document writing", summary: "Writes the document.", reads: "03_notes.md",
    writes: "04_document.md", is_model_used: true, is_on: true },
  { number: 5, name: "review", summary: "Reviews the document.", reads: "04_document.md", writes: "05_review/review.md",
    is_model_used: true, is_on: true },
  { number: 6, name: "lessons", summary: "Learns from the critiques.", reads: "the critiques",
    writes: "06_lessons/anti-patterns.md", is_model_used: true, is_on: true },
];

const NOW_S = 1_790_000_000;

function buildInboxRun(name: string, status: string, stoppedPhase: number, hasDocument: boolean) {
  return { root: "output", name, status, is_live: false, has_document: hasDocument, topic: "", display_name: "",
    stopped_phase: stoppedPhase };
}

export const INBOX: InboxItem[] = [
  { name: "AcmeSync kickoff.mp4", video: "inbox/AcmeSync kickoff.mp4", path: "inbox/AcmeSync kickoff.mp4",
    size_bytes: 254_000_000, run_folder: "", output: "output/AcmeSync kickoff", is_audio: false, is_finished: false,
    runs: [], parts: [], topic: "", display_name: "", tags: [], state: "new", state_at: NOW_S },
  { name: "Gateway review.mp4", video: "inbox/Gateway review.mp4", path: "inbox/Gateway review.mp4",
    size_bytes: 512_000_000, run_folder: "output/gateway-review", output: "output/gateway-review", is_audio: false,
    is_finished: true, runs: [buildInboxRun("gateway-review", "complete", 0, true)], parts: [],
    topic: "Gateway rollout plan", display_name: "", state: "done", state_at: NOW_S - 3600 },
  { name: "Jonas onboarding.m4a", video: "inbox/Jonas onboarding.m4a", path: "inbox/Jonas onboarding.m4a",
    size_bytes: 41_000_000, run_folder: "output/jonas-onboarding", output: "output/jonas-onboarding", is_audio: true,
    is_finished: false, runs: [buildInboxRun("jonas-onboarding", "blocked", 3, false)], parts: [], topic: "",
    display_name: "", state: "started", state_at: NOW_S - 600 },
];

function buildRunSummary(name: string, change: Partial<RunSummary>): RunSummary {
  return {
    root: "output", name, folder: `output/${name}`, path: `/work/output/${name}`, run_id: "20261020-101500",
    status: "complete", started_at: "2026-10-20T10:15:00", video: "", is_live: false, has_document: false, topic: "",
    tags: [], display_name: "", is_audio: false, ingesting_profile: "knowledge", settings_profile: "",
    is_profile_chosen: true, updated_at: NOW_S, state_at: NOW_S, stopped_phase: 0, ...change,
  };
}

export const RUNS: RunSummary[] = [
  buildRunSummary("gateway-review", { video: "inbox/Gateway review.mp4", has_document: true,
    topic: "Gateway rollout plan", tags: ["acmesync", "gateway"], state_at: NOW_S - 3600 }),
  buildRunSummary("jonas-onboarding", { video: "inbox/Jonas onboarding.m4a", status: "blocked", is_audio: true,
    stopped_phase: 3, state_at: NOW_S - 600 }),
  // a run folder whose recording is in no inbox list: it stands in the inbox's Done list by itself
  buildRunSummary("acmesync-retro", { video: "", has_document: true, topic: "AcmeSync sprint retrospective",
    file_type: "pdf", size_bytes: 27_803_017,
    tags: ["acmesync"], state_at: NOW_S - 7200 }),
];

export function buildJob(jobId: string, change: Partial<Job>, request: Partial<Job["request"]>): Job {
  return {
    job_id: jobId, status: "queued", pid: null, exit_code: null, queued_at: "2026-10-22T09:30:00", started_at: "",
    ended_at: "", command: ["python", "main.py", "run", "--video", "inbox/x.mp4"], is_output_missing: false,
    request: { video: "", output_root: "", only_phase: null, from_phase: null, to_phase: null, worker_indexes: [],
      is_resuming: false, is_dry_run: false, ...request },
    ...change,
  };
}

export const JOBS: Job[] = [
  buildJob("job-finished", { status: "complete", exit_code: 0, pid: 4242, started_at: "2026-10-22T08:00:00",
    ended_at: "2026-10-22T09:00:00", command: ["python", "main.py", "run", "--video", "inbox/Gateway review.mp4",
      "--output", "output/gateway-review"] },
  { video: "inbox/Gateway review.mp4", output_root: "output/gateway-review" }),
  buildJob("job-waiting", {}, { video: "inbox/Gateway demo.mp4", output_root: "output/gateway-demo" }),
];

export const JOB_CONSOLE_TEXT = "run header: Gateway review\nphase 1 transcribe complete\nphase 4 document writing complete\n";

const PHASE_NAMES = PHASES.map((phase) => phase.name);

function buildRunDetail(summary: RunSummary, statuses: string[], change: Partial<RunDetail>): RunDetail {
  return {
    ...summary,
    phases: PHASES.map((phase, index) => ({ number: phase.number, name: phase.name, title: phase.title,
      status: statuses[index], has_worker: phase.is_model_used && statuses[index] !== "not run" })),
    frames: {}, document_stages: [], is_document_total_known: true, blocked_items: [], step_outcomes: [],
    worker_feedback: { entries: 0, reports: 0, newest_kind: "", newest_summary: "", file: "" },
    seconds_per_frame: 0,
    critique: { files: 0, rules: 0, anti_patterns: 0, top: "", top_batches: 0, top_frames: 0, rule_set: "" },
    timing: { phases: Object.fromEntries(PHASE_NAMES.map((name) => [name, 120])), total_s: 720, is_counting: false },
    has_recording: false, is_recording_kept: false, can_keep_recording: false, recording_parts: [], batches: [],
    live_pids: [], log: `output/${summary.name}/logs/run.log`, signal: null,
    ...change,
  };
}

export const RUN_DETAILS: Record<string, RunDetail> = {
  "gateway-review": buildRunDetail(RUNS[0], ["complete", "complete", "complete", "complete", "complete", "complete"], {
    frames: { written: 38, skipped: 2 }, seconds_per_frame: 42, workflow: "quality",
    critique: { files: 4, rules: 9, anti_patterns: 3, top: "Quote the speaker's own numbers with their unit.",
      top_batches: 3, top_frames: 7, rule_set: "06_lessons/anti-patterns.md" },
  }),
  "jonas-onboarding": buildRunDetail(RUNS[1], ["complete", "complete", "blocked", "not run", "off", "off"], {
    blocked_items: [],
    step_outcomes: [{ phase: 3, step: "prepare items", blocked: 1, accepted: 0, reset: 0,
      blocks: [{ reason: "PREPARED_SECTION_MISSING", summary: "the Owner section was left out", at: "2026-10-22T09:10:00" }] }],
  }),
  "acmesync-retro": buildRunDetail(RUNS[2], ["complete", "complete", "complete", "complete", "complete", "complete"], {}),
};

export const RUN_LOG_TEXT = "phase 3 frames processing: batch 1 accepted\nphase 6 lessons complete\n";

export const SETTINGS: SettingRow[] = [
  { key: "framesProcessing.framesPerCall", value: 7, kind: "number", source: "playbook.local.json",
    comment: "How many consecutive frames one call reads." },
  { key: "review.isOn", value: true, kind: "bool", source: "code", comment: "Whether phase 5 reviews the document." },
  { key: "frames.mode", value: "change", kind: "text", source: "playbook.local.json", choices: ["change", "interval"],
    comment: "When a frame is taken." },
];

export const SETTINGS_PROFILES = { profiles: ["strict", "fast"] };

export const PHASE_MODELS: PhaseModels = {
  models: [{ name: "frame", spec: "lmstudio:acme-vision-35b" }, { name: "small", spec: "lmstudio:acme-vision-4b" }],
  phases: [{ phase: "frames_processing", number: 3, title: "frames processing", default: "frame" },
    { phase: "finalize", number: 4, title: "document writing", default: "frame" }],
};

export const KNOWN_TAGS: KnownTags = {
  tags: ["acmesync", "gateway"],
  profiles: [{ name: "meeting", aliases: ["call"], description: "a meeting with decisions" }],
};

export const INGESTING_PROFILES = { profiles: [
  { name: "knowledge", aliases: ["reference"], description: "a reference document by subject (the default)" },
  ...KNOWN_TAGS.profiles] };

export const DOWNLOAD_PARTS: DownloadPart[] = [{ key: "document", label: "Published document and its figures" }];

// The done runs' videos for Compress videos: two to compress, one compressed already
export const COMPRESSIBLE_VIDEOS: CompressibleVideo[] = [
  { root: "output", name: "gateway-review", display_name: "Gateway review", video: "Gateway review.mp4", is_in_inbox: false,
    size_bytes: 512e6, state: "not compressed", progress: 0, error: "", before_bytes: 0, after_bytes: 0 },
  { root: "output", name: "acmesync-kickoff", display_name: "AcmeSync kickoff with a long name that has to wrap on a phone",
    video: "AcmeSync kickoff.mp4", is_in_inbox: true, size_bytes: 254e6, state: "not compressed", progress: 0, error: "",
    before_bytes: 0, after_bytes: 0 },
  { root: "output", name: "jonas-onboarding", display_name: "Jonas onboarding", video: "Jonas onboarding.mp4",
    is_in_inbox: false, size_bytes: 90e6, state: "compressed", progress: 1, error: "", before_bytes: 300e6, after_bytes: 90e6 },
];

export const RUN_DEFAULTS = { pdf_mode: "by-size", pdf_images_page_limit: 20, pdf_model_writing_page_limit: 300,
  pdf_ocr_engine: "tesseract",
  knowledge_filter_model: "google-SigLIP-2", workflow: "quality", speed: SPEED_OPTIONS,
  document_wishes: { cleanup: "remove every Jira link", rewrite: "write each list of fields as a table",
    knowledge: "keep the field tables" } };

// Everything a run needs is there: the top bar shows no "Setup needed"
export const SETUP_READY: SetupCheck = {
  has_models: true,
  runtimes: [
    { key: "lmstudio", name: "LM Studio", kind: "host", purpose: "Runs the models on this machine.",
      download_url: "https://lmstudio.ai", key_variable: "", steps: { mac: ["Install it"], windows: ["Install it"],
        linux: ["Install it"] }, start_steps: ["Start its server"], is_needed: true, is_installed: true, is_running: true,
      available_models: ["acme-vision-35b"] },
    { key: "ffmpeg", name: "ffmpeg", kind: "tool", purpose: "Reads the recording.", download_url: "https://ffmpeg.org",
      key_variable: "", steps: { mac: ["brew install ffmpeg"], windows: ["winget install ffmpeg"],
        linux: ["apt install ffmpeg"] }, start_steps: [], is_needed: true, is_installed: true, is_running: null,
      available_models: [] },
  ],
  models: [{ name: "frame", spec: "lmstudio:acme-vision-35b", used_by: ["frames processing"], is_configured: true,
    is_on_host: true, recommended_repository: "", is_downloaded: true }],
  download: { is_running: false, log: "" },
  recommended: [],
};

// ffmpeg is missing: the top bar offers "Setup needed"
export const SETUP_TOOL_MISSING: SetupCheck = {
  ...SETUP_READY,
  runtimes: SETUP_READY.runtimes.map((runtime) => (runtime.key === "ffmpeg" ? { ...runtime, is_installed: false } : runtime)),
};

// No model configured: the wizard opens by itself
export const SETUP_NO_MODELS: SetupCheck = {
  ...SETUP_READY,
  has_models: false,
  runtimes: SETUP_READY.runtimes.map((runtime) => (runtime.key === "lmstudio" ? { ...runtime, is_needed: false } : runtime)),
  models: [],
};

// Accounts and projects. By default the server has no account and
// asks nobody to sign in, and holds one project, Default, so the top bar shows no project menu.
export const AUTH_NO_ACCOUNTS: AuthState = { has_accounts: false, account: null, roles: ["viewer", "operator", "admin"],
  is_sign_in_required: false };

export const ACCOUNT_ROWS: AccountRow[] = [{ name: "Jonas", role: "admin", created: "2026-10-20T10:00:00" }];

const DEFAULT_PROJECT: Project = { slug: "default", title: "Default", runs: 2, role: null, members: {}, groups: {} };

export const PROJECTS_DEFAULT_ONLY: ProjectsState = { projects: [DEFAULT_PROJECT], groups: [] };

// Two projects: gateway-review is in Project C, every other run in Default
export const PROJECTS_TWO: ProjectsState = {
  projects: [DEFAULT_PROJECT, { slug: "project-c", title: "Project C", runs: 1, role: null, members: {}, groups: {} }],
  groups: [],
};
