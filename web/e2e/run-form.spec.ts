// The run form: a new video's page, where the person chooses how the video is processed and starts its run.
import { expect, test } from "./fixtures/mockApi";
import type { ApiMock } from "./fixtures/mockApi";
import type { Page } from "@playwright/test";
import { INBOX, RUN_DEFAULTS } from "./fixtures/data";
import { expectInsideScreen, SCREEN_WIDTHS } from "./fixtures/layout";

async function openNewVideo(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "new ›" }).click();
  await expect(page.getByRole("heading", { name: "AcmeSync kickoff.mp4", level: 2 })).toBeVisible();
}

// A toggle pair's choice: the radio of that name inside the radiogroup of that name
function choice(page: Page, group: string, name: string) {
  return page.getByRole("radiogroup", { name: group, exact: true }).getByRole("radio", { name, exact: true });
}

test("offers the choices a video run takes", async ({ page }) => {
  await openNewVideo(page);

  await expect(choice(page, "process as", "Video")).toHaveAttribute("aria-checked", "true");
  // the Workflow speed names the preset the settings hold
  await expect(page.getByRole("button", { name: "workflow speed: Quality" })).toBeVisible();
  // with nothing changed, Start run names nothing more; the phases, settings and models are behind its gear
  await expect(page.getByRole("button", { name: /^Start run/ })).toHaveText(/^\s*Start run\s*$/);
  await page.getByRole("button", { name: "phases, settings and models" }).click();
  await expect(page.getByText("Phases: every phase")).toBeVisible();
  await expect(page.getByText("Run with default settings, default models")).toBeVisible();
  await page.keyboard.press("Escape");
  // the ingesting profile is chosen only for an audio run
  await expect(page.getByRole("combobox", { name: "ingesting profile" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Recording" })).toBeVisible();
});

test("as audio only, the workflow goes and the ingesting profile appears", async ({ page }) => {
  await openNewVideo(page);

  await choice(page, "process as", "Audio only").click();
  await expect(page.getByRole("button", { name: /^workflow speed/ })).toHaveCount(0);
  const profile = page.getByRole("combobox", { name: "ingesting profile" });
  await expect(profile).toHaveValue("");
  await profile.selectOption("meeting");
  await expect(profile).toHaveValue("meeting");
});

test("starts the run with every choice made on the form", async ({ page, api }) => {
  await openNewVideo(page);

  await page.getByRole("button", { name: "workflow speed: Quality" }).click();
  await page.locator(".speed-panel").getByRole("radio", { name: /^Balanced/ }).click();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "phases, settings and models" }).click();
  await page.getByText(/^Run with /).click();
  // the form has no settings-file menu: the settings files, strict among them, are chosen on the Settings page
  await expect(page.getByRole("radiogroup", { name: "Settings", exact: true })).toHaveCount(0);
  await choice(page, "model of document writing", "acme-vision-4b").click();
  await expect(page.getByText("Run with default settings, models: 1 changed")).toBeVisible();

  await page.getByText(/^Phases: /).click();
  await choice(page, "which phases", "Only").click();
  await page.getByRole("radiogroup", { name: "phase", exact: true }).getByRole("radio", { name: /^2 · / }).click();
  await page.keyboard.press("Escape");
  // what differs from the defaults shows on Start run itself
  const startRun = page.getByRole("button", { name: /^Start run/ });
  await expect(startRun).toContainText("phase 2 · default settings · models: 1 changed");

  await startRun.click();
  // the mocked queue holds one waiting job, so the run is queued behind it
  await expect(page.getByText("Queued AcmeSync kickoff.mp4 behind 1 job")).toBeVisible();
  const sent = api.sentTo("POST /jobs");
  expect(sent).toHaveLength(1);
  expect(sent[0].body).toMatchObject({
    items: [{ video: "inbox/AcmeSync kickoff.mp4", output: "output/AcmeSync kickoff" }],
    speed_preset: "balanced", only_phase: 2, phase_models: { finalize: "small" },
    is_run_as_audio: false,
  });
});

test("a tag added on the form is saved for the video at once", async ({ page, api }) => {
  api.answer("PUT /inbox/tags", (request) => ({ json: request.body }));
  await openNewVideo(page);

  await page.getByRole("button", { name: "add a tag" }).click();
  await page.keyboard.type("gateway");
  await page.keyboard.press("Enter");

  await expect(page.getByRole("button", { name: "remove tag gateway" })).toBeVisible();
  await expect.poll(() => api.sentTo("PUT /inbox/tags").length).toBe(1);
  expect(api.sentTo("PUT /inbox/tags")[0].body).toEqual({ video: "inbox/AcmeSync kickoff.mp4", tags: ["gateway"] });
});

for (const size of SCREEN_WIDTHS) {
  test(`a Word file's page says it is converted with no model and offers no range, process or workflow, on a ${size.width} px screen`,
    async ({ page, api }) => {
      await page.setViewportSize(size);
      const word = { ...INBOX[0], name: "Gateway guide.docx", video: "inbox/Gateway guide.docx",
        path: "inbox/Gateway guide.docx", output: "output/Gateway guide", file_type: "office" };
      api.answer("GET /inbox", { json: [word, ...INBOX.slice(1)] });
      await page.goto("/");
      await page.getByRole("button", { name: "new ›" }).click();
      await expect(page.getByRole("heading", { name: "Gateway guide.docx", level: 2 })).toBeVisible();
      const note = page.getByText(/Converted to markdown with no model/);
      await expect(note).toBeVisible();
      await expectInsideScreen(page, note);
      await expect(page.getByRole("button", { name: /Range/ })).toHaveCount(0);
      await expect(page.getByRole("radiogroup", { name: "process as" })).toHaveCount(0);
      await expect(page.getByRole("button", { name: /^workflow speed/ })).toHaveCount(0);
      await expect(page.getByRole("combobox", { name: "knowledge filter model" })).toHaveCount(0);
      await expect(page.getByRole("tab", { name: "Recording" })).toHaveCount(0);
    });
}

for (const size of SCREEN_WIDTHS) {
  test(`a slide deck's page says its slides are read as pictures and offers no range or workflow, on a ${size.width} px screen`,
    async ({ page, api }) => {
      await page.setViewportSize(size);
      const deck = { ...INBOX[0], name: "Gateway review.pptx", video: "inbox/Gateway review.pptx",
        path: "inbox/Gateway review.pptx", output: "output/Gateway review", file_type: "ppt" };
      api.answer("GET /inbox", { json: [deck, ...INBOX.slice(1)] });
      await page.goto("/");
      await page.getByRole("button", { name: "new ›" }).click();
      await expect(page.getByRole("heading", { name: "Gateway review.pptx", level: 2 })).toBeVisible();
      const note = page.getByText(/Each slide is drawn as a picture/);
      await expect(note).toBeVisible();
      await expectInsideScreen(page, note);
      await expect(page.getByRole("button", { name: /Range/ })).toHaveCount(0);
      await expect(page.getByRole("button", { name: /^workflow speed/ })).toHaveCount(0);
      await expect(page.getByRole("tab", { name: "Recording" })).toHaveCount(0);
    });
}

for (const size of SCREEN_WIDTHS) {
  test(`a done run's speed pill opens the Workflow speed menu, and Run from 3 takes the speed chosen, at ${size.width} px`,
    async ({ page, api }) => {
      // The owner: a done run read as Fast is run again from phase 3 as Balanced, from its own page
      await page.setViewportSize(size);
      await page.goto("/");
      await page.getByRole("button", { name: /^Done\s*2/ }).click();
      await page.getByText("Gateway review.mp4").click();
      await page.getByRole("button", { name: "workflow speed: Quality" }).click();
      const menu = page.locator(".speed-panel");
      await expectInsideScreen(page, menu);
      await menu.getByRole("radio", { name: /^Balanced/ }).click();
      await page.keyboard.press("Escape");
      await expect(page.getByRole("button", { name: "workflow speed: Balanced" })).toBeVisible();
      await page.getByRole("button", { name: "phase 3 actions" }).click();
      await page.getByRole("menuitem", { name: /Run from 3/ }).click();
      await expect.poll(() => api.sentTo("POST /jobs").length).toBe(1);
      expect(api.sentTo("POST /jobs")[0].body).toMatchObject({ from_phase: 3, speed_preset: "balanced" });
    });
}

async function openPdf(page: Page, api: ApiMock, pageCount: number) {
  const pdf = { ...INBOX[0], name: "Gateway guide.pdf", video: "inbox/Gateway guide.pdf", path: "inbox/Gateway guide.pdf",
    output: "output/Gateway guide", page_count: pageCount };
  api.answer("GET /inbox", { json: [pdf, ...INBOX.slice(1)] });
  await page.goto("/");
  await page.getByRole("button", { name: "new ›" }).click();
  await expect(page.getByRole("heading", { name: "Gateway guide.pdf", level: 2 })).toBeVisible();
}

test("a PDF's document shaping follows its processing, with a box for a model pass's extra wishes", async ({ page, api }) => {
  await openPdf(page, api, 120);
  const processing = page.getByRole("combobox", { name: "processing" });
  const shaping = page.getByRole("combobox", { name: "document shaping" });
  // Text offers the markdown shapings and knowledge; the profiles of people speaking are never a PDF's
  await expect(shaping.locator("option")).toHaveText(["default: LLM-written document", "Markdown",
    "Markdown + LLM clean up", "Markdown + LLM rewrite", "Markdown + LLM knowledge reshaping", "LLM-written document"]);
  // the model's knowledge reshaping is phase 7 over the markdown, never the knowledge profile phase 4 writes
  await shaping.selectOption({ label: "Markdown + LLM knowledge reshaping" });
  await expect(page.getByRole("textbox", { name: "extra wishes for the model" })).toHaveValue(
    RUN_DEFAULTS.document_wishes.knowledge);
  await shaping.selectOption({ label: "Markdown" });
  // no wishes box while no model pass is chosen
  await expect(page.getByRole("textbox", { name: "extra wishes for the model" })).toHaveCount(0);
  // each pass loads the wishes it was last given, its own
  await shaping.selectOption({ label: "Markdown + LLM rewrite" });
  const wishes = page.getByRole("textbox", { name: "extra wishes for the model" });
  await expect(wishes).toHaveValue(RUN_DEFAULTS.document_wishes.rewrite);
  await shaping.selectOption({ label: "Markdown + LLM clean up" });
  await expect(wishes).toHaveValue(RUN_DEFAULTS.document_wishes.cleanup);
  await wishes.fill("remove every Jira link and every user mention");
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    for (const part of [processing, shaping, wishes]) await expectInsideScreen(page, part);
  }

  await page.getByRole("button", { name: /^Start run/ }).click();
  await expect(page.getByText(/Queued Gateway guide\.pdf/)).toBeVisible();
  expect(api.sentTo("POST /jobs")[0].body).toMatchObject({
    pdf_mode: "markdown-conversion", document_pass: "cleanup",
    document_wishes: "remove every Jira link and every user mention",
  });

  // page pictures are always written by a model: the standing shapings leave the list, and the wishes box goes
  await processing.selectOption({ label: "Pages as images" });
  await expect(shaping.locator("option", { hasText: "Markdown" })).toHaveCount(0);
  await expect(wishes).toHaveCount(0);
});

for (const [pages, processing, shaping] of [[8, "Pages as images", "LLM-written document"], [120, "Text", "LLM-written document"],
  [1493, "Text", "Markdown"]] as const) {
  test(`a PDF of ${pages} pages names the defaults its size picks`, async ({ page, api }) => {
    await openPdf(page, api, pages);
    await expect(page.getByRole("combobox", { name: "processing" })).toHaveText(new RegExp(`default: ${processing} \\(${pages} pages\\)`));
    await expect(page.getByRole("combobox", { name: "document shaping" }).locator("option:checked"))
      .toHaveText(`default: ${shaping}`);
  });
}
