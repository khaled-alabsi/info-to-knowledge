// The run form's Range: a new recording's page, where the person chooses the part of the input the
// run processes: a start and an end time of a video or an audio recording, a first and a last page of a PDF.
import { expect, test } from "./fixtures/mockApi";
import type { ApiMock } from "./fixtures/mockApi";
import type { Page } from "@playwright/test";
import { INBOX } from "./fixtures/data";
import { expectInsideScreen, SCREEN_WIDTHS } from "./fixtures/layout";

// one grey pixel, the answer to every preview picture
const PIXEL_PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAAAAAA6fptVAAAACklEQVR4nGNoAAAAggCBd81ytgAAAABJRU5ErkJggg==",
  "base64");
const VIDEO = { ...INBOX[0], duration_s: 600 };
const AUDIO = { ...INBOX[0], name: "Jonas call.m4a", video: "inbox/Jonas call.m4a", path: "inbox/Jonas call.m4a",
  output: "output/Jonas call", is_audio: true, file_type: "audio", duration_s: 90 };
const PDF = { ...INBOX[0], name: "Gateway guide.pdf", video: "inbox/Gateway guide.pdf", path: "inbox/Gateway guide.pdf",
  output: "output/Gateway guide", file_type: "pdf", page_count: 10, duration_s: 0 };

async function openRecording(page: Page, api: ApiMock, item: typeof VIDEO) {
  api.answer("GET /inbox", { json: [item, ...INBOX.slice(1)] });
  api.answer("GET /inbox/preview/frame", { body: PIXEL_PNG, headers: { "content-type": "image/png" } });
  api.answer("GET /inbox/preview/page", { body: PIXEL_PNG, headers: { "content-type": "image/png" } });
  api.answer("GET /inbox/preview/waveform", { json: { peaks: Array.from({ length: 160 }, (_, at) => (at % 10) / 10) } });
  await page.goto("/");
  await page.getByRole("button", { name: "new ›" }).click();
  await expect(page.getByRole("heading", { name: item.name, level: 2 })).toBeVisible();
}

function rangeButton(page: Page) {
  return page.getByRole("button", { name: /^Range/ });
}

test("an untouched range processes the whole recording and sends no part", async ({ page, api }) => {
  await openRecording(page, api, VIDEO);

  await expect(rangeButton(page)).toContainText("Whole recording");
  await rangeButton(page).click();
  await expect(page.getByRole("region", { name: "the part to process" })).toContainText("Processing 10:00 of 10:00 (100%)");
  await expect(page.locator(".range-film img")).toHaveCount(12);
  await page.getByRole("button", { name: /^Start run/ }).click();
  await expect.poll(() => api.sentTo("POST /jobs").length).toBe(1);
  const body = api.sentTo("POST /jobs")[0].body as Record<string, unknown>;
  expect(body.range_start ?? "").toBe("");
  expect(body.range_end ?? "").toBe("");
});

test("a video's part is set by its handles and its fields, and the run is sent it", async ({ page, api }) => {
  await openRecording(page, api, VIDEO);
  await rangeButton(page).click();

  // a handle moves by ten seconds with Shift and an arrow key
  const start = page.getByRole("slider", { name: "start of processing" });
  await start.focus();
  await page.keyboard.press("Shift+ArrowRight");
  await page.keyboard.press("Shift+ArrowRight");
  await expect(start).toHaveAttribute("aria-valuetext", "0:20");
  // a typed end, taken on Enter
  await page.getByRole("textbox", { name: "end time" }).fill("5:00");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("slider", { name: "end of processing" })).toHaveAttribute("aria-valuetext", "5:00");
  await expect(rangeButton(page)).toContainText("0:20 to 5:00");
  await expect(page.locator(".range-summary")).toContainText("Processing 4:40 of 10:00 (47%)");

  await page.getByRole("button", { name: /^Start run/ }).click();
  await expect.poll(() => api.sentTo("POST /jobs").length).toBe(1);
  expect(api.sentTo("POST /jobs")[0].body).toMatchObject({ range_start: "0:20", range_end: "5:00", range_pages: "" });
});

test("a wrong time is refused under the fields, and Whole recording takes the range back", async ({ page, api }) => {
  await openRecording(page, api, VIDEO);
  await rangeButton(page).click();

  await page.getByRole("textbox", { name: "start time" }).fill("12:00");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("alert")).toHaveText("The start must come before the end, 10:00.");
  await page.getByRole("textbox", { name: "start time" }).fill("ten");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("alert")).toHaveText("Write the start as m:ss or h:mm:ss.");

  await page.getByRole("textbox", { name: "start time" }).fill("1:00");
  await page.keyboard.press("Enter");
  await expect(rangeButton(page)).toContainText("1:00 to 10:00");
  await page.getByRole("button", { name: /^Whole/ }).click();
  await expect(rangeButton(page)).toContainText("Whole recording");
});

test("an audio recording shows its waveform", async ({ page, api }) => {
  await openRecording(page, api, AUDIO);
  await rangeButton(page).click();

  await expect(page.locator(".range-wave rect")).toHaveCount(160);
  await expect(page.locator(".range-film")).toHaveCount(0);
});

test("a PDF's pages are chosen by a click and a shift-click, and the run is sent them", async ({ page, api }) => {
  await openRecording(page, api, PDF);

  await expect(rangeButton(page)).toContainText("All 10 pages");
  await rangeButton(page).click();
  const pages = page.getByRole("listbox", { name: "the PDF's pages" }).getByRole("option");
  await expect(pages).toHaveCount(10);
  await pages.nth(2).click();
  await pages.nth(5).click({ modifiers: ["Shift"] });
  await expect(rangeButton(page)).toContainText("Pages 3 to 6");
  await expect(page.getByRole("spinbutton", { name: "first page" })).toHaveValue("3");
  await expect(page.getByRole("spinbutton", { name: "last page" })).toHaveValue("6");
  await expect(pages.nth(4)).toHaveAttribute("aria-selected", "true");
  await expect(pages.nth(6)).toHaveAttribute("aria-selected", "false");

  // a last page past the PDF's end is refused
  await page.getByRole("spinbutton", { name: "last page" }).fill("12");
  await page.keyboard.press("Enter");
  await expect(page.getByRole("alert")).toHaveText("The pages run from 1 to 10.");

  await page.getByRole("spinbutton", { name: "last page" }).fill("8");
  await page.keyboard.press("Enter");
  await page.getByRole("button", { name: /^Start run/ }).click();
  await expect.poll(() => api.sentTo("POST /jobs").length).toBe(1);
  expect(api.sentTo("POST /jobs")[0].body).toMatchObject({ range_pages: "3-8", range_start: "", range_end: "" });
});

// A done video run's Copy and run again menu: Choose other times… opens the run's own recording in the editor, the part
// it processes now chosen to start from, and Process sends the part chosen for a new run folder
async function openOtherTimes(page: Page, api: ApiMock) {
  api.answer("GET /runs/output/gateway-review/input", { json: { recording: "Gateway review.mp4", is_audio: false,
    duration_s: 1200, page_count: 0, range_start: "", range_end: "10:00", range_pages: "" } });
  api.answer("GET /runs/output/gateway-review/preview/frame", { body: PIXEL_PNG, headers: { "content-type": "image/png" } });
  api.answer("POST /runs/output/gateway-review/part", { json: { exit_code: 0, output: "branched",
    records: [{ name: "gateway-review", folder: "output/gateway-review" }], jobs: [{ job_id: "job-part" }] } });
  await page.goto("/");
  await page.getByRole("button", { name: /^Done\s*2/ }).click();
  await page.getByText("Gateway review.mp4").click();
  await expect(page.getByRole("heading", { name: "gateway-review", level: 2 })).toBeVisible();
  await page.getByRole("button", { name: /Copy and run again/ }).click();
  await page.getByRole("menuitem", { name: /Choose other times/ }).click();
  return page.getByRole("dialog", { name: /Process another part of gateway-review/ });
}

test("a done run's other times are chosen over its own recording and sent for a new run folder", async ({ page, api }) => {
  const dialog = await openOtherTimes(page, api);

  await expect(dialog).toContainText("Now: 0:00 to 10:00.");
  // the part the run processes now cannot be sent again
  await expect(dialog.getByRole("button", { name: /^Process/ })).toBeDisabled();
  // no player stands beside it: no playhead and no "Start at" buttons
  await expect(dialog.getByRole("button", { name: /Start at/ })).toHaveCount(0);
  await dialog.getByRole("textbox", { name: "start time" }).fill("10:00");
  await dialog.getByRole("textbox", { name: "end time" }).fill("20:00");
  await dialog.getByRole("textbox", { name: "end time" }).press("Enter");
  await dialog.getByRole("textbox", { name: "start time" }).press("Enter");
  await expect(dialog.getByRole("button", { name: /^Process/ })).toHaveText(/Process 10:00 to 20:00/);
  await dialog.getByRole("button", { name: /^Process/ }).click();
  await expect.poll(() => api.sentTo("POST /runs/output/gateway-review/part").length).toBe(1);
  expect(api.sentTo("POST /runs/output/gateway-review/part")[0].body).toEqual({ range_start: "10:00", range_end: "",
    range_pages: "" });
  await expect(page.getByText(/copied to gateway-review for the part you chose; phases 1 on queued \(job job-part\)/))
    .toBeVisible();
});

for (const { width, height } of SCREEN_WIDTHS) {
  test(`a done run's Another part dialog lies inside a ${width} px screen`, async ({ page, api }) => {
    await page.setViewportSize({ width, height });
    const dialog = await openOtherTimes(page, api);
    await expect(dialog.locator(".range-film img")).toHaveCount(12);
    await expectInsideScreen(page, dialog);
  });

  test(`the range editor lies inside a ${width} px screen for a video, an audio recording and a PDF`, async ({ page, api }) => {
    await page.setViewportSize({ width, height });
    for (const item of [VIDEO, AUDIO, PDF]) {
      await openRecording(page, api, item);
      await rangeButton(page).click();
      const editor = page.getByRole("region", { name: "the part to process" });
      await expectInsideScreen(page, editor);
      await expectInsideScreen(page, rangeButton(page));
      const sideways = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(sideways, "the page does not scroll sideways").toBeLessThanOrEqual(0);
    }
  });
}
