// The top bar: the counts of running and queued jobs, the word for a server that does not answer, and the sidebar's tabs.
import { expect, test } from "./fixtures/mockApi";
import { buildJob, JOBS, RUNS } from "./fixtures/data";
import { expectInsideScreen, SCREEN_WIDTHS } from "./fixtures/layout";

test("shows how many jobs run and wait, and the sidebar's tabs with their counts", async ({ page, api }) => {
  await page.goto("/");

  const topBar = page.getByRole("banner");
  await expect(topBar.getByText("info-to-knowledge")).toBeVisible();
  await expect(topBar.getByText("0 running")).toBeVisible();
  await expect(topBar.getByText("1 queued")).toBeVisible();
  // one new video in the inbox, one job waiting in the queue
  await expect(page.getByRole("tab", { name: "Inbox 1" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Queue 1" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Manager" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Nothing open" })).toBeVisible();
  expect(api.sentTo("GET /phases").length).toBeGreaterThan(0);
});

test("the queued count pauses the queue only while two jobs are in it, and resumes it again defect", async ({ page, api }) => {
  // The pause was a button of its own beside "N queued"; the owner: merge the two.
  // Now the queued count opens the pause menu, once a running job and a waiting one are in the queue.
  await page.goto("/");
  await expect(page.getByRole("banner").getByText("1 queued")).toBeVisible();
  await expect(page.getByRole("button", { name: /queued/ })).toHaveCount(0);

  api.answer("GET /jobs", { json: [buildJob("job-running", { status: "running", pid: 4243 }, {}), ...JOBS] });
  let pause = { until: "", is_until_resumed: false };
  api.answer("GET /queue/pause", () => ({ json: pause }));
  api.answer("POST /queue/pause", (request) => {
    const minutes = (request.body as { minutes?: number }).minutes;
    pause = minutes ? { until: "2099-01-01T10:15:00", is_until_resumed: false } : { until: "", is_until_resumed: true };
    return { json: pause };
  });
  api.answer("POST /queue/resume", () => {
    pause = { until: "", is_until_resumed: false };
    return { json: pause };
  });
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await page.goto("/");
    await page.getByRole("button", { name: "1 queued", exact: true }).click();
    await expectInsideScreen(page, page.getByRole("menu"));
    // the choices run from 30 minutes to 6 hours (the owner)
    await expect(page.getByRole("menuitem", { name: /^Pause (30 minutes|1 hour|2 hours|4 hours|6 hours)$/ }))
      .toHaveCount(5);
    await page.getByRole("menuitem", { name: "Pause 2 hours", exact: true }).click();
    const paused = page.getByRole("button", { name: "1 queued · paused until 10:15" });
    await expect(paused).toBeVisible();
    await paused.click();
    await expectInsideScreen(page, page.getByRole("menu"));
    await page.getByRole("menuitem", { name: /Resume now/ }).click();
    await expect(page.getByRole("button", { name: "1 queued", exact: true })).toBeVisible();
  }
  expect(api.sentTo("POST /queue/pause")[0].body).toEqual({ minutes: 120 });
  expect(api.sentTo("POST /queue/resume").length).toBe(SCREEN_WIDTHS.length);
});

test("the queue paused until resumed reads Paused and waits for Resume now defect", async ({ page, api }) => {
  // Pause queue offered only 5 to 60 minutes, each ending by itself; a pause with no time limit was missing.
  // Now "Until I resume" holds the queue until Resume now.
  api.answer("GET /jobs", { json: [buildJob("job-running", { status: "running", pid: 4243 }, {}), ...JOBS] });
  let pause = { until: "", is_until_resumed: false };
  api.answer("GET /queue/pause", () => ({ json: pause }));
  api.answer("POST /queue/pause", () => {
    pause = { until: "", is_until_resumed: true };
    return { json: pause };
  });
  api.answer("POST /queue/resume", () => {
    pause = { until: "", is_until_resumed: false };
    return { json: pause };
  });
  await page.goto("/");
  await page.getByRole("button", { name: "1 queued", exact: true }).click();
  await page.getByRole("menuitem", { name: /Pause until I resume/ }).click();
  const paused = page.getByRole("button", { name: "1 queued · paused", exact: true });
  await expect(paused).toBeVisible();
  expect(api.sentTo("POST /queue/pause")[0].body).toEqual({});
  await paused.click();
  await page.getByRole("menuitem", { name: /Resume now/ }).click();
  await expect(page.getByRole("button", { name: "1 queued", exact: true })).toBeVisible();
});

test("the running count pauses or stops every running run at once defect", async ({ page, api }) => {
  // The owner found no way to stop or pause every run: the stop was a switch in the queued menu, shown only with two
  // jobs. Now the running count's menu has Pause all and Pause all now (the owner's names for them).
  api.answer("GET /jobs", { json: [buildJob("job-running", { status: "running", pid: 4243 }, {})] });
  api.answer("GET /runs", { json: RUNS.map((run, index) => index > 1 ? run : { ...run, is_live: true, status: "running" }) });
  let pause = { until: "", is_until_resumed: false };
  api.answer("GET /queue/pause", () => ({ json: pause }));
  api.answer("POST /queue/pause", () => {
    pause = { until: "", is_until_resumed: true };
    return { json: pause };
  });
  api.answer("POST /queue/resume", () => {
    pause = { until: "", is_until_resumed: false };
    return { json: pause };
  });
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await page.goto("/");
    await page.getByRole("button", { name: /2 running/ }).click();
    await expectInsideScreen(page, page.getByRole("menu"));
    await expect(page.getByRole("menuitem", { name: new RegExp(RUNS[0].name) })).toBeVisible();
    await page.getByRole("menuitem", { name: /^Pause all(?! now)/ }).click();
    await expect(page.getByRole("button", { name: /queued · paused/ })).toBeVisible();
    // while paused, Pause all is gone and Pause all now stays
    await page.getByRole("button", { name: /2 running/ }).click();
    await expect(page.getByRole("menuitem", { name: /^Pause all(?! now)/ })).toHaveCount(0);
    await page.getByRole("menuitem", { name: /^Pause all now/ }).click();
    pause = { until: "", is_until_resumed: false };
  }
  const sent = api.sentTo("POST /queue/pause").map((request) => request.body);
  expect(sent.slice(0, 2)).toEqual([{}, { is_stopping_now: true }]);
});

test("the queued count's pause menu offers Pause now, which stops every running run at once defect", async ({ page, api }) => {
  // Defect: the queue's own pause menu only held the next jobs; stopping the running runs too sat in another menu (the
  // owner: "I already have a global menu that can be enhanced"). Fix: it offers Pause now.
  api.answer("GET /jobs", { json: [buildJob("job-running", { status: "running", pid: 4243 }, {}),
    buildJob("job-next", { status: "queued" }, {})] });
  api.answer("GET /runs", { json: RUNS.map((run, index) => index > 0 ? run : { ...run, is_live: true, status: "running" }) });
  api.answer("GET /queue/pause", { json: { until: "", is_until_resumed: false } });
  api.answer("POST /queue/pause", { json: { until: "", is_until_resumed: true } });
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await page.goto("/");
    await page.getByRole("button", { name: /1 queued/ }).click();
    await expectInsideScreen(page, page.getByRole("menu"));
    await page.getByRole("menuitem", { name: /^Pause now/ }).click();
  }
  expect(api.sentTo("POST /queue/pause").map((request) => request.body)[0]).toEqual({ is_stopping_now: true });
});

test("says the server is not answering when its lists fail", async ({ page, api }) => {
  api.answer("GET /inbox", { status: 500, json: { detail: "down" } });
  await page.goto("/");

  await expect(page.getByRole("banner").getByText("server not answering")).toBeVisible();
  await expect(page.getByText("0 running")).toHaveCount(0);
});

test("the settings button opens the settings page and closes it again", async ({ page, api }) => {
  await page.goto("/");

  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Settings", level: 2 })).toBeVisible();
  // the sidebar goes while the settings are open
  await expect(page.getByRole("tab", { name: /Inbox/ })).toHaveCount(0);

  await page.getByRole("button", { name: "Close settings" }).click();
  await expect(page.getByRole("tab", { name: /Inbox/ })).toBeVisible();
  expect(api.sentTo("GET /settings").length).toBeGreaterThan(0);
});
