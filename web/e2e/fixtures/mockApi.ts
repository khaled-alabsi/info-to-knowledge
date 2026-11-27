// Answers every `/api/...` call the page makes with the invented data in `data.ts`, so no test needs `i2k serve`.
// A test changes one answer with `api.answer(...)` before it opens the page, and reads what the page sent from
// `api.sent`. A call no answer covers is answered 404 and fails the test after it ends, so a page that starts asking
// for something new is noticed here, not hidden behind an empty list.
import { test as base, expect } from "@playwright/test";
import type { Page, Route } from "@playwright/test";
import * as data from "./data";

export interface SentRequest {
  method: string;
  path: string;
  body: unknown;
}

type Reply = { status?: number; json?: unknown; body?: string | Buffer; headers?: Record<string, string> };
type Handler = (request: SentRequest) => Reply;

export interface ApiMock {
  // what the page sent, in order, polling included
  sent: SentRequest[];
  // the calls no answer covered
  unanswered: string[];
  // sets the answer to `METHOD /path` (without `/api`, without the query); a function sees the request
  answer: (key: string, reply: Reply | Handler) => void;
  // the requests sent to `METHOD /path`
  sentTo: (key: string) => SentRequest[];
}

const RUN_KEYS = Object.keys(data.RUN_DETAILS);

function buildDefaultHandlers(): Map<string, Handler> {
  const handlers = new Map<string, Handler>();
  const json = (value: unknown): Handler => () => ({ json: value });

  // 1. The lists the page polls, and what it reads once as it loads
  handlers.set("GET /inbox", json(data.INBOX));
  handlers.set("GET /runs", json(data.RUNS));
  handlers.set("GET /jobs", json(data.JOBS));
  // the queue is not paused until a test pauses it
  handlers.set("GET /queue/pause", json({ until: "" }));
  handlers.set("GET /phases", json(data.PHASES));
  handlers.set("GET /setup", json(data.SETUP_READY));
  handlers.set("GET /tags", json(data.KNOWN_TAGS));
  handlers.set("GET /inbox/trash", json([]));
  handlers.set("GET /inbox/archive", json([]));
  handlers.set("GET /auth/me", json(data.AUTH_NO_ACCOUNTS));
  handlers.set("GET /projects", json(data.PROJECTS_DEFAULT_ONLY));
  // the person's own lists, none until a test gives some; a save is answered with what was sent
  handlers.set("GET /lists", json({ lists: [] }));
  handlers.set("PUT /lists", (request) => ({ json: { lists: (request.body as { lists: unknown[] }).lists } }));
  // what a run takes when the form chooses nothing
  handlers.set("GET /run-defaults", json(data.RUN_DEFAULTS));
  // the worker sets the settings define: only the default, so no run form offers a choice until a test gives sets
  handlers.set("GET /worker-sets", json({ default_set: "default", sets: [{ name: "default", changes: [] }] }));

  // 2. What the run form, the run view and the settings ask for
  handlers.set("GET /profiles", json(data.SETTINGS_PROFILES));
  handlers.set("GET /phase-models", json(data.PHASE_MODELS));
  handlers.set("GET /ingesting-profiles", json(data.INGESTING_PROFILES));
  handlers.set("GET /settings", json(data.SETTINGS));
  handlers.set("GET /download/parts", json(data.DOWNLOAD_PARTS));
  // the new video's player asks for the recording; there is none, as a recording never reaches a test
  handlers.set("GET /inbox/video", () => ({ status: 404, json: { detail: "no recording in the tests" } }));

  // 3. Each run's and job's update, as the page polls it: the newest state and the log added since `offset`
  for (const name of RUN_KEYS) {
    handlers.set(`GET /runs/output/${name}/update`, () => ({
      json: { run: data.RUN_DETAILS[name], offset: data.RUN_LOG_TEXT.length, text: data.RUN_LOG_TEXT },
    }));
  }
  for (const job of data.JOBS) {
    handlers.set(`GET /jobs/${job.job_id}/update`, () => ({
      json: { job, offset: data.JOB_CONSOLE_TEXT.length, text: data.JOB_CONSOLE_TEXT },
    }));
  }

  // 4. The actions: each says it was done
  handlers.set("POST /jobs", (request) => {
    const items = (request.body as { items: { video: string; output: string }[] }).items;
    return { json: items.map((item, index) => ({ ...data.JOBS[1], job_id: `job-new-${index + 1}`,
      request: { ...data.JOBS[1].request, video: item.video, output_root: item.output } })) };
  });
  handlers.set("POST /jobs/job-waiting/cancel", () => ({ json: { ...data.JOBS[1], status: "cancelled" } }));
  handlers.set("PUT /settings", (request) => ({
    json: { exit_code: 0, output: `${(request.body as { key: string }).key} saved` },
  }));
  handlers.set("POST /setup/configure", () => ({ json: { exit_code: 0, output: "LM Studio is used for every phase" } }));
  handlers.set("POST /runs/display-names", () => ({ json: { records: [{ name: "gateway-review", outcome: "renamed" }] } }));
  handlers.set("GET /videos/compression", json(data.COMPRESSIBLE_VIDEOS));
  handlers.set("POST /videos/compression", () => ({ json: { pid: 4242 } }));
  // each asked run with one version of its document, so a download asks nothing unless a test answers otherwise
  handlers.set("POST /download/document-versions", (request) => ({
    json: ((request.body as { runs: { root: string; name: string }[] }).runs).map((run) => ({ ...run,
      versions: [{ number: 1, label: "original", path: `${run.name}/${run.name}.md` }] })),
  }));
  handlers.set("POST /download/documents", () => ({
    body: Buffer.from("PK\u0005\u0006" + "\u0000".repeat(18), "binary"),
    headers: { "Content-Type": "application/zip", "Content-Disposition": "attachment; filename*=UTF-8''documents.zip",
      "X-Skipped-Runs": "" },
  }));
  return handlers;
}

async function reply(route: Route, answer: Reply): Promise<void> {
  if (answer.json !== undefined) {
    await route.fulfill({ status: answer.status ?? 200, json: answer.json, headers: answer.headers });
    return;
  }
  await route.fulfill({ status: answer.status ?? 200, body: answer.body ?? "", headers: answer.headers });
}

async function installApiMock(page: Page): Promise<ApiMock> {
  const handlers = buildDefaultHandlers();
  const mock: ApiMock = {
    sent: [],
    unanswered: [],
    answer: (key, answer) => handlers.set(key, typeof answer === "function" ? answer : () => answer),
    sentTo: (key) => mock.sent.filter((request) => `${request.method} ${request.path}` === key),
  };

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api/, "");
    let body: unknown = null;
    try {
      body = request.postDataJSON();
    } catch {
      // a form upload or no body: kept as its text
      body = request.postData();
    }
    const sent = { method: request.method(), path, body };
    mock.sent.push(sent);
    const handler = handlers.get(`${sent.method} ${path}`);
    if (!handler) {
      mock.unanswered.push(`${sent.method} ${path}`);
      await route.fulfill({ status: 404, json: { detail: `no test answer for ${sent.method} ${path}` } });
      return;
    }
    await reply(route, handler(sent));
  });
  return mock;
}

// `auto`: every test gets the answers, whether or not it names `api`; a test that did not name it once reached the
// real server through the dev proxy.
export const test = base.extend<{ api: ApiMock }>({
  api: [async ({ page }, use) => {
    const mock = await installApiMock(page);
    await use(mock);
    expect([...new Set(mock.unanswered)], "calls the page made that no test answer covers").toEqual([]);
  }, { auto: true }],
});

export { expect };
