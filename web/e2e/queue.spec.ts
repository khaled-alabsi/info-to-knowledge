// The job queue and a job's console: what waits, taking a job off the queue, the finished jobs, and what a job's
// process printed.
import { expect, test } from "./fixtures/mockApi";
import type { ApiMock } from "./fixtures/mockApi";
import type { Page } from "@playwright/test";
import { buildJob } from "./fixtures/data";
import { expectInsideScreen, SCREEN_WIDTHS } from "./fixtures/layout";

// A job waiting for its second try, and one that failed its last try and holds the queue
const RETRYING = buildJob("job-retrying", { status: "retrying", exit_code: 1, attempt: 1, tries: 3,
  retry_at: "2026-10-22T10:42:05" }, { video: "inbox/Jonas onboarding.mp4", output_root: "output/jonas-onboarding" });
const BLOCKED = buildJob("job-blocked", { status: "blocked", exit_code: 1, attempt: 3, tries: 3 },
  { video: "inbox/AcmeSync retro.mp4", output_root: "output/acmesync-retro", on_failure: "block" });

function answerFailedJobs(api: ApiMock): void {
  api.answer("GET /jobs", { json: [RETRYING, BLOCKED] });
  for (const job of [RETRYING, BLOCKED]) {
    api.answer(`GET /jobs/${job.job_id}/update`, { json: { job, offset: 0, text: "" } });
  }
  api.answer("POST /jobs/job-blocked/retry", { json: { ...BLOCKED, status: "retrying" } });
  api.answer("POST /jobs/job-blocked/skip", { json: { ...BLOCKED, status: "incomplete" } });
  api.answer("POST /jobs/job-blocked/on-failure", { json: BLOCKED });
}

async function openBlockedJob(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByRole("tab", { name: /Queue/ }).click();
  await page.getByRole("button", { name: /acmesync-retro/ }).click();
}

test("a failed job shows which retry it waits for, and a job holding the queue is retried or skipped defect", async ({ page, api }) => {
  // A failed job was followed at once by the next job, and the page showed no retry. Now the queue row says which
  // retry the job waits for, and a job that holds the queue after its last try offers Retry and Skip
  answerFailedJobs(api);
  await page.goto("/");
  await page.getByRole("tab", { name: /Queue/ }).click();

  await expect(page.getByRole("button", { name: /jonas-onboarding/ })).toContainText("retry 1 of 2 at 10:42:05");
  await expect(page.getByRole("button", { name: /acmesync-retro/ })).toContainText("failed 3 tries");
  await page.getByRole("button", { name: "retry", exact: true }).click();
  await expect(page.getByText("Job job-blocked: runs again before any other job")).toBeVisible();
  expect(api.sentTo("POST /jobs/job-blocked/retry")).toHaveLength(1);
  await page.getByRole("button", { name: "skip", exact: true }).click();
  expect(api.sentTo("POST /jobs/job-blocked/skip")).toHaveLength(1);
});

test("a job's own choice for after its last try is set in its detail view", async ({ page, api }) => {
  answerFailedJobs(api);
  await openBlockedJob(page);

  await expect(page.getByText("failed 3 tries: the queue holds")).toBeVisible();
  // an on/off item of the On failure menu (the owner: the switch was "too much text"), on while the job holds the queue
  await page.getByRole("button", { name: "On failure" }).click();
  const choice = page.getByRole("menuitemcheckbox", { name: /Hold queue on failure/ });
  await expect(choice).toHaveAttribute("aria-checked", "true");
  await choice.click();
  await expect.poll(() => api.sentTo("POST /jobs/job-blocked/on-failure").map((sent) => sent.body))
    .toEqual([{ choice: "continue" }]);
});

test("a job with no choice of its own shows the queue's setting it takes, never 'as in Settings'", async ({ page, api }) => {
  // The owner: "resolve 'as in setting'": the switch stands where queue.onFailure puts it
  answerFailedJobs(api);
  const unset = { ...BLOCKED, request: { ...BLOCKED.request, on_failure: "" as const } };
  api.answer("GET /jobs", { json: [RETRYING, unset] });
  api.answer("GET /jobs/job-blocked/update", { json: { job: unset, offset: 0, text: "" } });
  api.answer("GET /settings", { json: [{ key: "queue.onFailure", value: "block", kind: "text", source: "local",
    comment: "", choices: ["continue", "block"] }] });
  await openBlockedJob(page);

  await page.getByRole("button", { name: "On failure" }).click();
  const choice = page.getByRole("menuitemcheckbox", { name: /Hold queue on failure/ });
  await expect(choice).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText("as in Settings")).toHaveCount(0);
  await choice.click();
  await expect.poll(() => api.sentTo("POST /jobs/job-blocked/on-failure").map((sent) => sent.body))
    .toEqual([{ choice: "continue" }]);
});

for (const { width, height } of SCREEN_WIDTHS) {
  test(`a held job's Retry, Skip and its On failure menu stay inside a ${width} px screen`, async ({ page, api }) => {
    await page.setViewportSize({ width, height });
    answerFailedJobs(api);
    await openBlockedJob(page);

    await expectInsideScreen(page, page.getByRole("button", { name: "Retry", exact: true }));
    await expectInsideScreen(page, page.getByRole("button", { name: "Skip", exact: true }));
    await page.getByRole("button", { name: "On failure" }).click();
    await expectInsideScreen(page, page.getByRole("menuitemcheckbox", { name: /Hold queue on failure/ }));
    await page.screenshot({ path: `../temp/job-failure-switch/held-${width}.png` });
  });
}

test("lists the waiting job and takes it off the queue", async ({ page, api }) => {
  await page.goto("/");
  await page.getByRole("tab", { name: /Queue/ }).click();

  const waiting = page.getByRole("button", { name: /gateway-demo/ });
  await expect(waiting).toBeVisible();
  await expect(waiting).toContainText("Gateway demo.mp4 · every phase");

  await page.getByRole("button", { name: "cancel" }).click();
  await expect(page.getByText("Job job-waiting: taken off the queue")).toBeVisible();
  expect(api.sentTo("POST /jobs/job-waiting/cancel")).toHaveLength(1);
});

test("an empty queue says nothing is waiting", async ({ page, api }) => {
  api.answer("GET /jobs", { json: [] });
  await page.goto("/");
  await page.getByRole("tab", { name: /Queue/ }).click();

  await expect(page.getByText(/^Nothing is waiting\./)).toBeVisible();
  await expect(page.getByRole("button", { name: /Finished jobs/ })).toHaveCount(0);
});

test("a finished job opens its console with what its process printed", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("tab", { name: /Queue/ }).click();

  const finished = page.getByRole("button", { name: /Finished jobs/ });
  await expect(finished).toHaveAttribute("aria-expanded", "false");
  await finished.click();
  await page.getByRole("button", { name: /gateway-review/ }).click();

  // named as its Queue row names it: the run folder, the recording under it
  await expect(page.getByRole("heading", { name: "gateway-review", level: 2 })).toBeVisible();
  await expect(page.getByText("Gateway review.mp4 · job job-finished · every phase")).toBeVisible();
  await expect(page.getByText("main.py run --video inbox/Gateway review.mp4 --output output/gateway-review")).toBeVisible();
  await expect(page.getByText("Job console")).toBeVisible();
  await expect(page.getByText("phase 4 document writing complete")).toBeVisible();
  await expect(page.getByRole("button", { name: "Copy" })).toBeEnabled();

  await page.getByRole("button", { name: "back" }).click();
  await expect(page.getByRole("heading", { name: "Nothing open" })).toBeVisible();
});

test("a console's Copy copies on a page opened over plain http at a network address defect", async ({ page }) => {
  // Defect: on the page opened at http://192.168…, the browser offers no clipboard and Copy showed "Copy failed".
  // Fix: the text is copied through the browser's own copy command; the copied text is caught as it is copied.
  await page.addInitScript(() => {
    Object.defineProperty(Navigator.prototype, "clipboard", { get: () => undefined, configurable: true });
    const copied: string[] = [];
    (window as unknown as { copiedTexts: string[] }).copiedTexts = copied;
    document.addEventListener("copy", () => copied.push(String(window.getSelection() ?? "")
      || (document.activeElement as HTMLTextAreaElement | null)?.value || ""));
  });
  await page.goto("/");
  await page.getByRole("tab", { name: /Queue/ }).click();
  await page.getByRole("button", { name: /Finished jobs/ }).click();
  await page.getByRole("button", { name: /gateway-review/ }).click();
  await page.getByRole("button", { name: "Copy" }).click();

  await expect(page.getByRole("button", { name: "Copied" })).toBeVisible();
  const copied = await page.evaluate(() => (window as unknown as { copiedTexts: string[] }).copiedTexts);
  expect(copied.join("\n")).toContain("phase 4 document writing complete");
});
