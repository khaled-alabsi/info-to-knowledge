// What `./i2k serve` answers (src/services/web_api_service.py), faked for the page's tests: `fetch` by route,
// update source per stream, and `XMLHttpRequest` for uploads. Names are invented (AcmeSync, Jonas, Gateway).
import type { StreamUpdate } from "../updateSource";
import { vi } from "vitest";
import type { InboxItem, Job, Phase, PhaseRow, RunDetail, RunSummary } from "../api";

export interface RecordedCall {
  method: string;
  url: string;
  body: unknown;
}

type RouteAnswer = Response | object | string | number | boolean | null;
type RouteHandler = RouteAnswer | ((body: Record<string, unknown>, url: string) => RouteAnswer | Promise<RouteAnswer>);

export function answerJson(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

export function answerText(value: string, status = 200): Response {
  return new Response(value, { status, headers: { "Content-Type": "text/markdown" } });
}

// Routes are "GET /api/inbox"; a route not listed answers 404 with the server's `detail` shape.
export function installServer(routes: Record<string, RouteHandler>): RecordedCall[] {
  const calls: RecordedCall[] = [];
  const fetchFake = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    calls.push({ method, url, body });
    const handler = routes[`${method} ${url}`];
    if (handler === undefined) return answerJson({ detail: `no route ${method} ${url}` }, 404);
    const answer = typeof handler === "function" ? await handler(body ?? {}, url) : handler;
    // a ready-made reply is handed out again on every request: a copy each time, since a body can be read once
    return answer instanceof Response ? answer.clone() : answerJson(answer);
  });
  vi.stubGlobal("fetch", fetchFake);
  return calls;
}

export class FakeUpdateSource {
  static instances: FakeUpdateSource[] = [];
  isClosed = false;
  onupdate: ((update: StreamUpdate) => void) | null = null;
  onerror: ((status: number | null) => void) | null = null;

  constructor(private readonly buildUrl: () => string) {
    FakeUpdateSource.instances.push(this);
  }

  // the address the source asks now, with the offset its reader holds
  get url(): string {
    return this.buildUrl();
  }

  close() {
    this.isClosed = true;
  }

  emit<T extends StreamUpdate>(update: T) {
    this.onupdate?.(update);
  }

  // a failed ask; with a status, the server answered and refused (404: the run or job is gone)
  fail(status: number | null = null) {
    this.onerror?.(status);
  }

  static latest(): FakeUpdateSource {
    const source = FakeUpdateSource.instances.at(-1);
    if (!source) throw new Error("no update source was opened");
    return source;
  }
}

export class FakeXMLHttpRequest {
  static instances: FakeXMLHttpRequest[] = [];
  method = "";
  url = "";
  sent: FormData | null = null;
  status = 0;
  responseText = "";
  upload: { onprogress: ((event: ProgressEvent) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor() {
    FakeXMLHttpRequest.instances.push(this);
  }

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }

  send(form: FormData) {
    this.sent = form;
  }

  reportProgress(loaded: number, total: number, isComputable = true) {
    this.upload.onprogress?.(new ProgressEvent("progress", { loaded, total, lengthComputable: isComputable }));
  }

  respond(status: number, body: unknown) {
    this.status = status;
    this.responseText = typeof body === "string" ? body : JSON.stringify(body);
    this.onload?.();
  }

  failToConnect() {
    this.onerror?.();
  }

  static latest(): FakeXMLHttpRequest {
    const request = FakeXMLHttpRequest.instances.at(-1);
    if (!request) throw new Error("no upload was sent");
    return request;
  }
}

export const PHASES: Phase[] = [
  { number: 1, name: "transcribe", summary: "Turns the speech into a timed transcript.", reads: "the video",
    writes: "01_transcript.json", is_model_used: false },
  { number: 2, name: "frames_preparing", summary: "Takes a frame each time the shared screen changes.", reads: "the video",
    writes: "frames/*.jpg", is_model_used: false },
  { number: 3, name: "frames_processing", summary: "A vision model reads the frames in batches.", reads: "02_frames.json",
    writes: "03_notes.md", is_model_used: true },
  { number: 4, name: "finalize", summary: "One model conversation writes the document.", reads: "the notes",
    writes: "04_document.md", is_model_used: true },
  { number: 5, name: "review" },
  { number: 6, name: "critique_collection" },
];

export function makeRun(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    root: "knowledge",
    name: "AcmeSync-demo",
    folder: "/work/knowledge/AcmeSync-demo",
    path: "knowledge/AcmeSync-demo",
    tags: [],
    display_name: "",
    is_audio: false,
    ingesting_profile: "knowledge",
    is_profile_chosen: false,
    run_id: "20261001-120000",
    status: "incomplete",
    started_at: "2026-10-01T12:00:00",
    // each fake run folder records its own video unless a test gives two folders one video
    video: `/work/inbox/${overrides.name ?? "AcmeSync-demo"}.mp4`,
    is_live: false,
    has_document: false,
    topic: "",
    updated_at: 1790000000,
    state_at: 1790000000,
    stopped_phase: 0,
    ...overrides,
  };
}

export function makePhaseRows(statuses: string[]): PhaseRow[] {
  return statuses.map((status, index) => ({ number: index + 1, name: PHASES[index].name, status }));
}

export function makeRunDetail(overrides: Partial<RunDetail> = {}): RunDetail {
  return {
    ...makeRun(),
    phases: makePhaseRows(["complete", "complete", "not run", "not run", "not run", "not run"]),
    frames: { pending: 0, analyzed: 0, written: 0, skipped: 0, blocked: 0 },
    document_stages: [{ name: "table of contents", counts: { waiting: 2 } }, { name: "chapters", counts: {} },
      { name: "final summary", counts: { waiting: 1 } }],
    is_document_total_known: false,
    blocked_items: [],
    worker_feedback: { entries: 0, reports: 0, open: 0, newest_kind: "", newest_summary: "", newest_answer: "",
      file: "" },
    seconds_per_frame: 0,
    critique: { files: 0, rules: 0, anti_patterns: 0, top: "", top_batches: 0, top_frames: 0, rule_set: "" },
    step_outcomes: [],
    live_pids: [],
    log: "knowledge/AcmeSync-demo/logs/20261001-120000/12-00.log",
    signal: null,
    timing: { phases: {}, total_s: 0, is_counting: false },
    has_recording: true,
    is_recording_kept: false,
    can_keep_recording: false,
    recording_parts: [],
    batches: [],
    ...overrides,
  };
}

export function makeJob(overrides: Partial<Job> = {}, request: Partial<Job["request"]> = {}): Job {
  return {
    job_id: "job-1",
    request: {
      video: "/work/inbox/AcmeSync-demo.mp4",
      output_root: "/work/knowledge/AcmeSync-demo",
      only_phase: null,
      from_phase: null,
      to_phase: null,
      worker_indexes: [],
      is_resuming: false,
      is_dry_run: false,
      ...request,
    },
    status: "queued",
    pid: null,
    exit_code: null,
    queued_at: "2026-10-01T12:30:00",
    started_at: "",
    ended_at: "",
    command: ["i2k", "run", "--video", "/work/inbox/AcmeSync-demo.mp4"],
    is_output_missing: false,
    ...overrides,
  };
}

export function makeInboxItem(name: string, state: InboxItem["state"], overrides: Partial<InboxItem> = {}): InboxItem {
  return {
    name,
    video: `/work/inbox/${name}`,
    path: `inbox/${name}`,
    size_bytes: 250_000_000,
    run_folder: name.replace(/\.\w+$/, ""),
    output: `/work/knowledge/${name.replace(/\.\w+$/, "")}`,
    is_audio: /\.(mp3|m4a|wav|aac|flac|ogg|opus)$/i.test(name),
    is_finished: state === "done",
    runs: state === "new" ? [] : [{ root: "knowledge", name: name.replace(/\.\w+$/, ""), status: state === "done" ? "complete" : "incomplete",
      is_live: state === "running", has_document: state === "done", topic: "", display_name: "", stopped_phase: 0 }],
    state,
    state_at: 1790000000,
    topic: "",
    display_name: "",
    parts: [],
    ...overrides,
  };
}
