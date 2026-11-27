// The top bar: the counts of running and queued jobs, the word for a server that does not answer, and the sidebar's tabs.
import { expect, test } from "./fixtures/mockApi";
import { buildJob, JOBS } from "./fixtures/data";
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
  // the switch stops the running run at once, as Ctrl-C does, and leaves the menu open to pick the pause
  const stopNow = page.getByRole("menuitemcheckbox", { name: /Stop the running run now/ });
  await stopNow.click();
  await expect(stopNow).toHaveAttribute("aria-checked", "true");
  await page.getByRole("menuitem", { name: /Pause until I resume/ }).click();
  const paused = page.getByRole("button", { name: "1 queued · paused", exact: true });
  await expect(paused).toBeVisible();
  expect(api.sentTo("POST /queue/pause")[0].body).toEqual({ is_stopping_now: true });
  await paused.click();
  await page.getByRole("menuitem", { name: /Resume now/ }).click();
  await expect(page.getByRole("button", { name: "1 queued", exact: true })).toBeVisible();
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
