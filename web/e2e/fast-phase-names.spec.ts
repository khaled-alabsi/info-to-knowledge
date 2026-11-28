// A Fast run names its phase 3 "processing and draft writing" and its phase 4 "document reshaping" (it has no frame
// processing and no document writing from notes); every other run keeps "frames processing" and "document writing".
// Opened here: an ordinary run and a Fast one, the phase cards' names and the help behind a card's "?", at every width.
import { expect, test } from "./fixtures/mockApi";
import type { Page } from "@playwright/test";
import { INBOX, PHASES, RUN_DETAILS } from "./fixtures/data";
import { expectInsideScreen, SCREEN_WIDTHS } from "./fixtures/layout";

const FAST_TITLES: Record<number, string> = { 3: "processing and draft writing", 4: "document reshaping" };

async function openDoneRun(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: /^Done\s*2/ }).click();
  await page.getByText("Gateway review.mp4").click();
  await expect(page.getByRole("heading", { name: "gateway-review", level: 2 })).toBeVisible();
}

async function expectCardNames(page: Page, three: string, four: string) {
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    const cards = page.locator(".stepper .step");
    await expect(cards.nth(2).locator(".step-name")).toHaveText(three);
    await expect(cards.nth(3).locator(".step-name")).toHaveText(four);
    await expectInsideScreen(page, cards.nth(2).locator(".step-name"));
    await expectInsideScreen(page, cards.nth(3).locator(".step-name"));
  }
}

test("an ordinary run's phases 3 and 4 are frames processing and document writing, at every width", async ({ page }) => {
  await openDoneRun(page);
  await expectCardNames(page, "frames processing", "document writing");
  await page.getByRole("button", { name: "what phase 4 does" }).click();
  await expect(page.getByText("Phase 4 · document writing")).toBeVisible();
});

test("a Fast run's phases 3 and 4 are processing and draft writing and document reshaping, at every width", async ({ page, api }) => {
  const base = RUN_DETAILS["gateway-review"];
  const detail = { ...base, workflow: "direct-draft",
    phases: base.phases.map((phase) => (FAST_TITLES[phase.number] ? { ...phase, title: FAST_TITLES[phase.number] } : phase)) };
  api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
  api.answer("GET /phases", { json: PHASES.map((phase) => (FAST_TITLES[phase.number] ? { ...phase, title: FAST_TITLES[phase.number] } : phase)) });
  await openDoneRun(page);
  await expectCardNames(page, "processing and draft writing", "document reshaping");
  await expect(page.getByText("frames processing")).toHaveCount(0);
  await page.getByRole("button", { name: "what phase 4 does" }).click();
  await expect(page.getByText("Phase 4 · document reshaping")).toBeVisible();
  await expect(page.getByText("Phase 4 · document writing")).toHaveCount(0);
});

test("the pill of a stopped Fast run names its phase 3 by the run's title, an ordinary run's by the general one, at every width", async ({ page, api }) => {
  const [fresh, review, started] = INBOX;
  const fast = { ...started, name: "Jonas onboarding.mp4", is_audio: false,
    runs: [{ ...started.runs[0], phase_titles: { "3": "processing and draft writing", "4": "document reshaping" } }] };
  const ordinary = { ...started, name: "Acme planning.mp4", video: "inbox/Acme planning.mp4", path: "inbox/Acme planning.mp4",
    is_audio: false, runs: [{ ...started.runs[0], name: "acme-planning" }] };
  api.answer("GET /inbox", { json: [fresh, review, fast, ordinary] });
  await page.goto("/");
  const pills = page.getByText("phase 3 ›");
  await expect(pills).toHaveCount(2);
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await expect(pills.nth(0)).toHaveAttribute("title", /^stopped in phase 3, processing and draft writing\n/);
    await expect(pills.nth(1)).toHaveAttribute("title", /^stopped in phase 3, frames processing\n/);
    await expectInsideScreen(page, pills.nth(0));
    await expectInsideScreen(page, pills.nth(1));
  }
});
