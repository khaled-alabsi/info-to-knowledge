// The page on a small phone, 320 x 700 (the narrowest common one; 390 is the owner's): no view is wider than the screen, and the buttons the owner found broken (a finished
// run's actions, a new video's Start run and choices, the Manager's tools and tool, a menu) lay out on its width.
import { expect, test } from "./fixtures/mockApi";
import type { Locator, Page } from "@playwright/test";

test.use({ viewport: { width: 320, height: 700 } });

async function expectNoSideScroll(page: Page) {
  const widths = await page.evaluate(() => ({ page: document.documentElement.scrollWidth, screen: document.documentElement.clientWidth }));
  expect(widths.page, "the page is no wider than the screen").toBeLessThanOrEqual(widths.screen);
}

async function readBox(locator: Locator) {
  const box = await locator.boundingBox();
  if (!box) {
    throw new Error("the element has no box on the page");
  }
  return box;
}

async function expectInsideScreen(page: Page, locator: Locator) {
  const box = await readBox(locator);
  const screen = page.viewportSize()!.width;
  expect(box.x, "starts on the screen").toBeGreaterThanOrEqual(0);
  expect(box.x + box.width, "ends on the screen").toBeLessThanOrEqual(screen);
}

async function openDoneRun(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: /^Done\s*2/ }).click();
  await page.getByText("Gateway review.mp4").click();
  await expect(page.getByRole("heading", { name: "gateway-review", level: 2 })).toBeVisible();
}

test("the inbox list fits the screen", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("tab", { name: /Inbox/ })).toBeVisible();
  await expectNoSideScroll(page);
});

test("a finished run's actions wrap under its title, inside the screen defect", async ({ page }) => {
  // In one row the actions were 650 px wide on a 375 px screen and pushed the whole page sideways; on a phone they now
  // take their own lines under the title
  await openDoneRun(page);
  const actions = page.locator(".detail-head .head-actions");
  await expect(actions).toBeVisible();
  await expectNoSideScroll(page);
  await expectInsideScreen(page, actions);
  for (const button of await actions.getByRole("button").all()) {
    await expectInsideScreen(page, button);
  }
  const title = await readBox(page.getByRole("heading", { name: "gateway-review", level: 2 }));
  expect((await readBox(actions)).y, "the actions start under the title").toBeGreaterThanOrEqual(title.y + title.height);
});

test("a menu opens as wide as the phone allows, inside the screen defect", async ({ page }) => {
  // At its 220 px every item of a menu wrapped to two lines; on a phone it is 320 px or the screen less 24, on the screen
  await openDoneRun(page);
  await page.locator(".detail-head .head-actions").getByRole("button", { name: /Copy and run again/ }).click();
  const menu = page.locator(".dropdown-panel").last();
  await expect(menu).toBeVisible();
  await expectInsideScreen(page, menu);
  expect((await readBox(menu)).width).toBeGreaterThanOrEqual(Math.min(320, page.viewportSize()!.width - 24) - 1);
});

test("a new video's Start run stands under its title, and each choice beside its label defect", async ({ page }) => {
  // Start run squeezed the title to ten letters a line, and a label ended one line while its choice began the next
  await page.goto("/");
  await page.getByRole("button", { name: "new ›" }).click();
  const title = page.getByRole("heading", { name: "AcmeSync kickoff.mp4", level: 2 });
  await expect(title).toBeVisible();
  await expectNoSideScroll(page);
  const start = page.getByRole("button", { name: "Start run" });
  await expectInsideScreen(page, start);
  const titleBox = await readBox(title);
  expect((await readBox(start)).y, "Start run is under the title").toBeGreaterThanOrEqual(titleBox.y + titleBox.height - 1);
  // each choice is one box, its name inside it beside its buttons; Process comes first, then Workflow
  const boxes = page.locator(".video-head .run-actions > .field-toggle");
  const count = await boxes.count();
  expect(count).toBeGreaterThanOrEqual(2);
  for (let index = 0; index < count; index++) {
    const label = await readBox(boxes.nth(index).locator("small"));
    // its choice: a pair of buttons, or the Workflow speed's menu button
    const pair = await readBox(boxes.nth(index).locator(".toggle-pair, .btn").first());
    expect(Math.abs(label.y + label.height / 2 - (pair.y + pair.height / 2)), "the label is beside its choice").toBeLessThan(8);
    await expectInsideScreen(page, boxes.nth(index));
  }
  const process = await readBox(page.locator(".choice-process"));
  const workflow = await readBox(page.locator(".choice-workflow"));
  expect(process.y < workflow.y || (process.y === workflow.y && process.x < workflow.x), "Process before Workflow").toBe(true);
});

test("the queue fits the screen", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("tab", { name: /Queue/ }).click();
  await expectNoSideScroll(page);
});

test("the Manager's tools stand apart, and a tool opens right under the tabs defect", async ({ page }) => {
  // Stacked, the tools had 4 px between them and read as one block; a tool opened halfway down the screen, the page's
  // two rows stretched over its height
  await page.goto("/");
  await page.getByRole("tab", { name: "Manager" }).click();
  const tools = page.getByRole("navigation", { name: "manager tools" });
  const download = await readBox(tools.getByRole("button", { name: "Download" }));
  const rename = await readBox(tools.getByRole("button", { name: "Rename" }));
  expect(rename.y - (download.y + download.height), "the tools stand apart").toBeGreaterThanOrEqual(6);
  await expectNoSideScroll(page);

  await tools.getByRole("button", { name: "Download" }).click();
  const heading = page.getByRole("heading", { name: "Download", level: 2 });
  await expect(heading).toBeVisible();
  const tabs = await readBox(page.getByRole("tab", { name: "Manager" }));
  expect((await readBox(heading)).y - (tabs.y + tabs.height), "the tool starts right under the tabs").toBeLessThan(80);
  await expectNoSideScroll(page);
});

test("the settings fit the screen", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Settings", level: 2 })).toBeVisible();
  await expectNoSideScroll(page);
});

test("a run's file groups stay one line each, and its rename button stays beside its title defect", async ({ page, api }) => {
  // Beside the live note, "Dropped frames" and "Audio and transcript" broke inside their chips, and the rename pencil
  // wrapped alone onto the line under the title
  api.answer("GET /runs/output/gateway-review/artifacts", { json: [
    { key: "notes", title: "Frame notes", kind: "markdown", items: [{ path: "03_notes.md", name: "03_notes.md", size_bytes: 10 }] },
    { key: "dropped", title: "Dropped frames", kind: "images", items: [] },
    { key: "audio", title: "Audio and transcript", kind: "audio", items: [] }] });
  api.answer("GET /runs/output/gateway-review/files/03_notes.md", { body: "# Gateway notes" });
  await openDoneRun(page);
  await page.getByRole("tab", { name: "Artifacts" }).click();
  const chips = page.locator(".artifact-bar .chip");
  await expect(chips.first()).toBeVisible();
  // the mocked run is not live and its title is short, so nothing squeezes them here: the rules are checked as well
  expect(await chips.first().evaluate((chip) => getComputedStyle(chip).whiteSpace), "a chip never breaks its text").toBe("nowrap");
  expect(await page.locator(".title-row > h2").first().evaluate((title) => getComputedStyle(title).flexBasis),
    "the title shrinks beside its pencil").toBe("0px");
  for (const chip of await chips.all()) {
    expect((await readBox(chip)).height, "a chip is one line").toBeLessThan(40);
    await expectInsideScreen(page, chip);
  }
  const title = await readBox(page.getByRole("heading", { name: "gateway-review", level: 2 }));
  const pencil = await readBox(page.locator(".title-row > button").first());
  expect(pencil.y, "the pencil starts above the title's bottom").toBeLessThan(title.y + title.height);
  await expectNoSideScroll(page);
});
