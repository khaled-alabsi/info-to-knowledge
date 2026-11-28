// The Settings tab: settings grouped by section and card, a change shown as a draft until the top bar's Save settings writes
// it, a search that opens the groups holding a match, a profile's own file, and the two model sections: the model servers (one
// card each, holding its models) and the provider sets (one menu per job).
import { expect, test } from "./fixtures/mockApi";
import type { Locator, Page } from "@playwright/test";
import { SETTINGS } from "./fixtures/data";
import { expectInsideScreen, SCREEN_WIDTHS } from "./fixtures/layout";

// Brings a control into view and waits until the page has drawn the scroll: a menu opened by a click closes on a scroll
// event, and the scroll Playwright makes for a click arrives after the click when the control is out of view
async function scrollIntoViewSettled(page: Page, control: Locator) {
  await control.scrollIntoViewIfNeeded();
  await page.evaluate(() => new Promise<void>((done) => requestAnimationFrame(() => requestAnimationFrame(() => done()))));
}

async function openSettings(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Settings", level: 2 })).toBeVisible();
}

// Opens a section, then one of its cards: both stand closed until opened
async function openCard(page: Page, section: string, card: string) {
  await page.getByRole("heading", { name: section, exact: true, level: 3 }).click();
  await page.getByRole("heading", { name: card, exact: true, level: 4 }).click();
}

test("lists every setting under its section and card, each closed until opened", async ({ page }) => {
  await openSettings(page);

  await expect(page.getByRole("heading", { name: "Review and quality", level: 3 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Frames", exact: true, level: 3 })).toBeVisible();
  const framesPerCall = page.getByRole("spinbutton", { name: "framesProcessing.framesPerCall" });
  await expect(framesPerCall).toBeHidden();

  await page.getByRole("heading", { name: "Frames", exact: true, level: 3 }).click();
  // an opened section shows its cards' titles alone
  await expect(page.getByRole("heading", { name: "Frame processing", exact: true, level: 4 })).toBeVisible();
  await expect(framesPerCall).toBeHidden();
  await page.getByRole("heading", { name: "Frame processing", exact: true, level: 4 }).click();
  await expect(framesPerCall).toHaveValue("7");
  // the help text comes from the page's own labels, not the settings file's comment
  await expect(page.getByText("How many consecutive frames the model reads in one call; 1 means one call for each frame.")).toBeVisible();
  // a saved value carries no "changed" mark, whichever file set it
  await expect(page.getByText("changed", { exact: true })).toHaveCount(0);
});

test("a change is a draft until Save settings writes it", async ({ page, api }) => {
  await openSettings(page);
  await openCard(page, "Frames", "Frame processing");

  const framesPerCall = page.getByRole("spinbutton", { name: "framesProcessing.framesPerCall" });
  await framesPerCall.fill("9");
  const save = page.getByRole("button", { name: "Save settings (1)" });
  await expect(save).toBeVisible();
  expect(api.sentTo("PUT /settings")).toHaveLength(0);

  await save.click();
  await expect(page.getByText("1 setting saved; the next run reads them")).toBeVisible();
  await expect(save).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Close settings" })).toBeVisible();
  const sent = api.sentTo("PUT /settings");
  expect(sent).toHaveLength(1);
  expect(sent[0].body).toEqual({ key: "framesProcessing.framesPerCall", new_value: 9, profile: "", key_parts: null });
});

test("typing the old value back takes the draft away", async ({ page }) => {
  await openSettings(page);
  await openCard(page, "Review and quality", "Review");

  const reviewSwitch = page.getByRole("checkbox", { name: "review.isOn" });
  await reviewSwitch.uncheck();
  await expect(page.getByRole("button", { name: "Save settings (1)" })).toBeVisible();
  await reviewSwitch.check();
  await expect(page.getByRole("button", { name: /Save settings/ })).toHaveCount(0);
});

test("a refused setting stays a draft and says why", async ({ page, api }) => {
  api.answer("PUT /settings", { json: { exit_code: 1, output: "frames.mode: not one of change, interval" } });
  await openSettings(page);
  await openCard(page, "Frames", "Frame capturing");

  // a choice reads in plain words, never the word the settings file holds
  await page.getByRole("button", { name: "frames.mode: When the screen changes" }).click();
  await page.getByRole("menuitemradio", { name: "At a fixed rate" }).click();
  await page.getByRole("button", { name: "Save settings (1)" }).click();
  await expect(page.getByText("frames.mode: not one of change, interval")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save settings (1)" })).toBeVisible();
});

test("the search opens each group holding a match and hides the rest", async ({ page }) => {
  await openSettings(page);

  await page.getByRole("searchbox").fill("consecutive");
  await expect(page.getByRole("spinbutton", { name: "framesProcessing.framesPerCall" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Review and quality", level: 3 })).toHaveCount(0);
});

test("choosing a profile reads its file and drops the drafts typed for the default settings", async ({ page, api }) => {
  await openSettings(page);
  await openCard(page, "Frames", "Frame processing");
  await page.getByRole("spinbutton", { name: "framesProcessing.framesPerCall" }).fill("9");
  await expect(page.getByRole("button", { name: "Save settings (1)" })).toBeVisible();

  const readsBefore = api.sentTo("GET /settings").length;

  await page.getByRole("button", { name: "Edit: default settings" }).click();
  await page.getByRole("menuitemradio", { name: "profile: fast" }).click();
  // the unsaved change is named before it is dropped, the warning inside the screen at every width
  const warning = page.getByRole("alert");
  await expect(warning).toContainText("You have 1 unsaved change to the default settings");
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await expectInsideScreen(page, warning.getByRole("button", { name: "Discard the changes and switch" }));
  }
  await warning.getByRole("button", { name: "Discard the changes and switch" }).click();
  await expect(page.getByRole("button", { name: /Save settings/ })).toHaveCount(0);
  await expect(page.getByText(/Profile fast sets/)).toBeVisible();
  await expect.poll(() => api.sentTo("GET /settings").length).toBeGreaterThan(readsBefore);
});

test("each LLM pass's extra wishes have their own box in the PDF card, inside the screen at every width", async ({ page, api }) => {
  api.answer("GET /settings", { json: [...SETTINGS,
    { key: "pdf.cleanupWishes", value: "remove every Jira link", kind: "long-text", source: "playbook.local.json",
      comment: "The extra wishes of phase 7's LLM clean up." },
    { key: "pdf.rewriteWishes", value: "", kind: "long-text", source: "code",
      comment: "The extra wishes of phase 7's LLM rewrite." }] });
  await openSettings(page);
  await openCard(page, "Document", "PDFs");

  const cleanup = page.getByRole("textbox", { name: "pdf.cleanupWishes" });
  await expect(cleanup).toHaveValue("remove every Jira link");
  await expect(cleanup).toHaveJSProperty("tagName", "TEXTAREA");
  await expect(page.getByRole("textbox", { name: "pdf.rewriteWishes" })).toHaveJSProperty("tagName", "TEXTAREA");
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await expectInsideScreen(page, cleanup);
  }
});

// The model sections: each server is a card with its address, token variable and jobs, and the models that run on it inside;
// each provider set is a card of only the values it changes, beside the default's
const PROVIDER_SET_ROWS = [...SETTINGS,
  { key: "providerSets", value: { spare: { machine: "the office", framesProcessing: { framesPerCall: 5 } } }, kind: "json", source: "playbook.local.json", comment: "" },
  { key: "defaultProviderSet", value: "default", kind: "text", source: "code", comment: "" },
  { key: "machine", value: "the laptop", kind: "text", source: "playbook.local.json", comment: "" },
  { key: "hosts", value: { lmstudio: { url: "http://localhost:1234/v1" }, "gpu-box": { url: "http://gpu-box.local:8090/v1" } },
    kind: "json", source: "playbook.local.json", comment: "" },
  { key: "models.frame.spec", path: ["models", "frame", "spec"], value: "lmstudio:acme-vision-35b", kind: "text", source: "code", comment: "" },
  { key: "models.frame.host", path: ["models", "frame", "host"], value: "", kind: "text", source: "code", comment: "",
    resolved: "lmstudio", resolved_note: "from the model's provider" },
  { key: "models.frame-gpu.spec", path: ["models", "frame-gpu", "spec"], value: "lmstudio:gpu-vision", kind: "text",
    source: "playbook.local.json", comment: "" },
  { key: "models.frame-gpu.host", path: ["models", "frame-gpu", "host"], value: "gpu-box", kind: "text",
    source: "playbook.local.json", comment: "" },
  { key: "phaseModels.frames_processing", path: ["phaseModels", "frames_processing"], value: "", kind: "text", source: "code",
    comment: "", resolved: "frame" },
  { key: "audio.language", path: ["audio", "language"], value: null, kind: "text", source: "code", comment: "",
    resolved: null, resolved_note: "found from the recording" }];

test("a server card holds its address, its models and its jobs, a new model is added on its server, all inside the screen", async ({ page, api }) => {
  api.answer("GET /settings", { json: PROVIDER_SET_ROWS });
  await openSettings(page);
  await page.getByRole("heading", { name: "Model servers", exact: true, level: 3 }).click();

  const local = page.getByRole("group", { name: "server lmstudio" });
  const gpu = page.getByRole("group", { name: "server gpu-box" });
  await expect(local.getByRole("textbox", { name: "address of lmstudio" })).toHaveValue("http://localhost:1234/v1");
  await expect(gpu.getByRole("textbox", { name: "address of gpu-box" })).toHaveValue("http://gpu-box.local:8090/v1");
  // a model stands in the card of the server it runs on, and its name is a plain box, never the joined "lmstudio:..." text
  await expect(local.getByRole("group", { name: "model frame", exact: true })).toBeVisible();
  await expect(gpu.getByRole("group", { name: "model frame-gpu" })).toBeVisible();
  await expect(local.getByRole("group", { name: "model frame-gpu" })).toHaveCount(0);
  const frame = local.getByRole("group", { name: "model frame", exact: true });
  await frame.locator("summary").first().click();
  const spec = frame.getByRole("textbox", { name: "Model name of frame" });
  await expect(spec).toHaveValue("acme-vision-35b");
  await expect(frame.getByText("lmstudio:acme-vision-35b")).toHaveCount(0);
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await expectInsideScreen(page, spec);
    await expectInsideScreen(page, gpu.getByRole("textbox", { name: "address of gpu-box" }));
  }

  await gpu.getByRole("textbox", { name: "address of gpu-box" }).fill("http://gpu-box.local:9000/v1");
  await gpu.getByRole("button", { name: "add model to gpu-box" }).click();
  await gpu.getByRole("textbox", { name: "New model's name on gpu-box" }).fill("frame-gpu-two");
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await expectInsideScreen(page, gpu.getByRole("textbox", { name: "New model's name on gpu-box" }));
    await expectInsideScreen(page, gpu.getByRole("button", { name: "Cancel" }));
  }
  await gpu.getByRole("button", { name: "Add", exact: true }).click();
  const added = gpu.getByRole("group", { name: "model frame-gpu-two" });
  await expect(added).toBeVisible();
  await expect(added.getByText("unsaved", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Save settings (2)" }).click();
  await expect.poll(() => api.sentTo("PUT /settings").length).toBe(2);
  const bodies = api.sentTo("PUT /settings").map((call) => call.body as { key: string; new_value: Record<string, unknown> });
  expect(bodies.map((body) => body.key).sort()).toEqual(["hosts", "models.frame-gpu-two"]);
  expect(bodies.find((body) => body.key === "hosts")?.new_value).toMatchObject({ "gpu-box": { url: "http://gpu-box.local:9000/v1" } });
});

for (const size of SCREEN_WIDTHS) {
  test(`each provider set is a card of only the values it changes, and a change is saved as the set's override, at ${size.width} px`,
    async ({ page, api }) => {
      api.answer("GET /settings", { json: PROVIDER_SET_ROWS });
      await page.setViewportSize(size);
      await openSettings(page);
      await page.getByRole("heading", { name: "Provider sets", level: 3 }).click();

      // the default set shows each job with the library model it runs and where; another set shows what it leaves as the default's
      const fallback = page.getByRole("group", { name: "provider set default" });
      await expectInsideScreen(page, fallback);
      await expect(fallback.getByText("Runs on lmstudio · acme-vision-35b")).toBeVisible();
      await expect(fallback.getByRole("button", { name: "default: Frames processing: the frame model" })).toBeVisible();
      const chosen = page.getByRole("button", { name: "Used when a job names no set: default" });
      await expectInsideScreen(page, chosen);
      const spare = page.getByRole("group", { name: "provider set spare" });
      await expectInsideScreen(page, spare);
      const link = spare.getByRole("button", { name: "spare: Frames processing: same as default set: frame" });
      await expectInsideScreen(page, link);
      // a set has no Machine box: the machine is read from the servers its models run on
      await expect(spare.getByRole("button", { name: /^Machine of/ })).toHaveCount(0);
      await expect(spare.getByRole("button", { name: "remove provider set spare" })).toBeVisible();
      await expect(spare.getByText("default: 7")).toBeVisible();

      // a job is linked to a library model from the list of the library's names
      await scrollIntoViewSettled(page, link);
      await link.click();
      const menu = page.getByRole("menu", { name: "spare: Frames processing" });
      await expectInsideScreen(page, menu);
      await menu.getByRole("menuitemradio", { name: /frame-gpu/ }).click();
      await expect(spare.getByText("Runs on gpu-box · gpu-vision")).toBeVisible();

      const value = spare.getByRole("textbox", { name: "spare: framesProcessing.framesPerCall" });
      await value.fill("6");
      await page.getByRole("button", { name: "Save settings (1)" }).click();
      const sent = api.sentTo("PUT /settings");
      await expect.poll(() => sent.length).toBe(1);
      expect(sent[0].body).toMatchObject({ key: "providerSets", new_value: { spare: {
        phaseModels: { frames_processing: "frame-gpu" }, framesProcessing: { framesPerCall: 6 } } } });
    });
}

test("Copy on a provider set card creates a new set from it and asks for its name", async ({ page, api }) => {
  api.answer("GET /settings", { json: PROVIDER_SET_ROWS });
  await openSettings(page);
  await page.getByRole("heading", { name: "Provider sets", level: 3 }).click();
  const spare = page.getByRole("group", { name: "provider set spare" });
  await spare.getByRole("button", { name: "copy provider set spare" }).click();
  const create = spare.getByRole("button", { name: "Create copy" });
  await expect(create).toBeDisabled();
  await spare.getByRole("textbox", { name: "name of the copy of spare" }).fill("spare");
  await expect(spare.getByText("a set has this name")).toBeVisible();
  await spare.getByRole("textbox", { name: "name of the copy of spare" }).fill("spare two");
  await create.click();
  await expect(page.getByRole("group", { name: "provider set spare two" })).toBeVisible();
  await page.getByRole("button", { name: "Save settings (1)" }).click();
  const sent = api.sentTo("PUT /settings");
  await expect.poll(() => sent.length).toBe(1);
  expect(sent[0].body).toMatchObject({ key: "providerSets", new_value: {
    spare: { machine: "the office" }, "spare two": { machine: "the office", framesProcessing: { framesPerCall: 5 } } } });
});

test("the page keeps two model sections, Model servers then Provider sets, and a server's model is removed after asking", async ({ page, api }) => {
  api.answer("GET /settings", { json: PROVIDER_SET_ROWS });
  await openSettings(page);
  const headings = await page.getByRole("heading", { level: 3 }).allInnerTexts();
  const order = ["Model servers", "Provider sets"].map((title) => headings.findIndex((heading) => heading.startsWith(title)));
  expect(order.every((at) => at >= 0)).toBe(true);
  expect([...order].sort((first, second) => first - second)).toEqual(order);
  // the old sections are gone
  expect(headings.some((heading) => heading.startsWith("Model providers") || heading.startsWith("Models"))).toBe(false);

  await page.getByRole("heading", { name: "Model servers", exact: true, level: 3 }).click();
  const gpuModel = page.getByRole("group", { name: "model frame-gpu" });
  await gpuModel.locator("summary").first().click();
  await gpuModel.getByRole("button", { name: "remove model frame-gpu" }).click();
  await gpuModel.getByRole("button", { name: "yes, remove model frame-gpu" }).click();
  await expect.poll(() => api.sentTo("POST /settings/remove").map((call) => call.body)).toEqual([
    { key_parts: ["models", "frame-gpu"], profile: "" }]);
  // a model of the shipped settings can be changed, never removed
  const shipped = page.getByRole("group", { name: "model frame", exact: true });
  await shipped.locator("summary").first().click();
  await expect(shipped.getByRole("button", { name: "remove model frame" })).toHaveCount(0);
});

test("a server is added and removed as a card, never as JSON text, at every width", async ({ page, api }) => {
  api.answer("GET /settings", { json: PROVIDER_SET_ROWS });
  await openSettings(page);
  await page.getByRole("heading", { name: "Model servers", exact: true, level: 3 }).click();
  const remove = page.getByRole("button", { name: "remove server gpu-box" });
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await expectInsideScreen(page, remove);
    await expectInsideScreen(page, page.getByRole("textbox", { name: "address of gpu-box" }));
  }
  await expect(page.locator("textarea")).toHaveCount(0);

  await page.getByRole("button", { name: "+ Add server" }).click();
  const name = page.getByRole("textbox", { name: "New server's name" });
  await name.fill("lmstudio");
  await expect(page.getByText("a server has this name")).toBeVisible();
  await name.fill("laptop");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByRole("group", { name: "server laptop" })).toBeVisible();
  await remove.click();
  await expect(page.getByRole("group", { name: "server gpu-box" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Save settings (1)" })).toBeVisible();
});

// A model card shows its model's name as a field inside its server's card, its text keeps clear of the card's left border on a
// phone, and a provider set links a job (speech to text) only to the models whose server does that job
const PROVIDER_ROWS = [...PROVIDER_SET_ROWS.filter((row) => row.key !== "hosts"),
  { key: "hosts", kind: "json", source: "playbook.local.json", comment: "", value: {
    lmstudio: { url: "http://localhost:1234/v1", kinds: ["chat"] },
    "gpu-box": { url: "http://gpu-box.local:8090/v1", kinds: ["chat", "speech"] } } },
  { key: "models.whisper-gpu.spec", path: ["models", "whisper-gpu", "spec"], value: "openai-compatible:whisper-large",
    kind: "text", source: "playbook.local.json", comment: "" },
  { key: "models.whisper-gpu.host", path: ["models", "whisper-gpu", "host"], value: "gpu-box", kind: "text",
    source: "playbook.local.json", comment: "" },
  { key: "providers.speech.libraryModel", path: ["providers", "speech", "libraryModel"], value: "", kind: "text", source: "code",
    comment: "" }];

for (const size of SCREEN_WIDTHS) {
  test(`a model card has a Model name field, and a server's job offers only models that do it, at ${size.width} px`,
    async ({ page, api }) => {
      api.answer("GET /settings", { json: PROVIDER_ROWS });
      await page.setViewportSize(size);
      await openSettings(page);
      await page.getByRole("heading", { name: "Model servers", exact: true, level: 3 }).click();
      const card = page.getByRole("group", { name: "model whisper-gpu" });
      await card.locator("summary").first().click();
      const name = card.getByRole("textbox", { name: "Model name of whisper-gpu" });
      await expect(name).toHaveValue("whisper-large");
      await expect(card.getByText("openai-compatible:whisper-large")).toHaveCount(0);
      await expectInsideScreen(page, name);
      // the card's own text starts clear of its left border
      const box = await card.boundingBox();
      const title = await card.locator("summary strong").boundingBox();
      expect(box && title && title.x - box.x).toBeGreaterThanOrEqual(8);

      await page.getByRole("heading", { name: "Provider sets", level: 3 }).click();
      const spare = page.getByRole("group", { name: "provider set spare" });
      const speech = spare.getByRole("button", { name: /^spare: Speech to text/ });
      await scrollIntoViewSettled(page, speech);
      await speech.click();
      const menu = page.getByRole("menu", { name: "spare: Speech to text" });
      await expectInsideScreen(page, menu);
      await expect(menu.getByRole("menuitemradio", { name: /gpu-box: whisper-gpu/ })).toBeVisible();
      // a model on the chat-only server is not offered to speech to text
      await expect(menu.getByRole("menuitemradio", { name: /lmstudio: frame(?!-)/ })).toHaveCount(0);
      await menu.getByRole("menuitemradio", { name: /gpu-box: whisper-gpu/ }).click();
      await page.getByRole("button", { name: "Save settings (1)" }).click();
      const sent = api.sentTo("PUT /settings");
      await expect.poll(() => sent.length).toBe(1);
      expect(sent[0].body).toMatchObject({ key: "providerSets", new_value: { spare: {
        providers: { speech: { libraryModel: "whisper-gpu" } } } } });
    });
}

// The owner: the model sections made no sense. Exactly two: the model servers (each server with the models on it and the jobs
// it does) and the provider sets, mapping every job to a model that can do it
const TWO_SECTION_ROWS = [...PROVIDER_ROWS.filter((row) => !row.key.startsWith("phaseModels.")),
  ...["frames_preparing", "frames_processing", "finalize", "review"].map((phase) => ({
    key: `phaseModels.${phase}`, path: ["phaseModels", phase], value: "", kind: "text", source: "code", comment: "", resolved: "frame" })),
  { key: "models.small-filter.spec", path: ["models", "small-filter", "spec"], value: "lmstudio:qwen3.5-4b", kind: "text",
    source: "default", comment: "" },
  { key: "models.small-filter.suits", path: ["models", "small-filter", "suits"], value: ["filtering"], kind: "json",
    source: "default", comment: "The kinds of job this model suits: `frames`, `writing` or `filtering`; empty suits every job." },
  { key: "models.frame.suits", path: ["models", "frame", "suits"], value: ["frames"], kind: "json", source: "default",
    comment: "The kinds of job this model suits: `frames`, `writing` or `filtering`; empty suits every job." },
  ...["chat", "diarization", "embeddings", "decision"].map((kind) => ({
    key: `providers.${kind}.libraryModel`, path: ["providers", kind, "libraryModel"], value: "", kind: "text", source: "code",
    comment: "The entry of `models` that does this job, over `host` and `timeoutS` below. \"\" keeps `host`." })),
  ...["speech", "diarization"].flatMap((kind) => ["host", "model", "timeoutS"].map((field) => ({
    key: `providers.${kind}.${field}`, path: ["providers", kind, field], value: field === "timeoutS" ? 0 : "", kind: field === "timeoutS" ? "number" : "text",
    source: "code", comment: "Where `providers.x` runs." })))];

for (const size of SCREEN_WIDTHS) {
  test(`the page has two model sections: servers with their models, and sets with a menu a job offering only models that suit it, at ${size.width} px`,
    async ({ page, api }) => {
      api.answer("GET /settings", { json: TWO_SECTION_ROWS });
      await page.setViewportSize(size);
      await openSettings(page);
      const headings = await page.getByRole("heading", { level: 3 }).allInnerTexts();
      expect(headings.slice(0, 2).map((heading) => heading.replace(/\s+\d.*$/s, "").trim())).toEqual(["Model servers", "Provider sets"]);
      for (const title of ["Model servers", "Provider sets"]) await page.getByRole("heading", { name: title, exact: true, level: 3 }).click();

      // a server card holds its jobs as tick boxes, and the models that run on it
      const gpu = page.getByRole("group", { name: "server gpu-box" });
      await expect(gpu.getByRole("checkbox", { name: "Speech to text, gpu-box" })).toBeChecked();
      await expect(gpu.getByRole("checkbox", { name: "Speaker detection, gpu-box" })).not.toBeChecked();
      await expectInsideScreen(page, gpu.getByRole("checkbox", { name: "Speech to text, gpu-box" }));

      // a model holds the jobs it suits as tick boxes
      const small = page.getByRole("group", { name: "model small-filter" });
      await small.locator("summary").first().click();
      await expect(small.getByRole("checkbox", { name: "Filtering frames, small-filter" })).toBeChecked();
      await expect(small.getByRole("checkbox", { name: "Reading pictures, small-filter" })).not.toBeChecked();
      await expectInsideScreen(page, small.getByRole("checkbox", { name: "Filtering frames, small-filter" }));

      // frames preparing offers the filter model, frames processing the picture reader, never the other way round
      const spare = page.getByRole("group", { name: "provider set spare" });
      await expectInsideScreen(page, spare);
      const preparing = spare.getByRole("button", { name: /^spare: Frames preparing/ });
      await scrollIntoViewSettled(page, preparing);
      await preparing.click();
      const preparingMenu = page.getByRole("menu", { name: "spare: Frames preparing" });
      await expect(preparingMenu.getByRole("menuitemradio", { name: /lmstudio: small-filter/ })).toBeVisible();
      await expect(preparingMenu.getByRole("menuitemradio", { name: /lmstudio: frame(?!-)/ })).toHaveCount(0);
      // an option names its server plainly, never an address
      await expect(preparingMenu).not.toContainText(/localhost|http|\.local/);
      await page.keyboard.press("Escape");
      const processing = spare.getByRole("button", { name: /^spare: Frames processing/ });
      await scrollIntoViewSettled(page, processing);
      await processing.click();
      const processingMenu = page.getByRole("menu", { name: "spare: Frames processing" });
      await expect(processingMenu.getByRole("menuitemradio", { name: /lmstudio: frame(?!-)/ })).toBeVisible();
      await expect(processingMenu.getByRole("menuitemradio", { name: /lmstudio: small-filter/ })).toHaveCount(0);
      await page.keyboard.press("Escape");

      // every job is its own card; no Machine box and no fast workflow model menus
      await expect(spare.getByRole("button", { name: /^Machine of/ })).toHaveCount(0);
      await expect(page.getByText(/Fast workflow|Frame describer|Document writer/)).toHaveCount(0);
      for (const job of ["Speech to text", "Scanned page reader", "Speaker detection", "Frame vectors", "Decision filter"]) {
        await expect(spare.getByRole("button", { name: new RegExp(`^spare: ${job}`) })).toHaveCount(1);
      }
      expect(await spare.locator(".job-link").count()).toBe(9);
      // no backtick and no quoted-empty pair anywhere on the page
      const text = await page.locator(".settings-view").innerText();
      expect(text).not.toContain("`");
      expect(text).not.toContain('""');
      await page.screenshot({ path: `../temp/settings-shots/two-sections-${size.width}.png`, fullPage: true });
    });
}

test("a set's value set back to the default leaves the set changing nothing", async ({ page, api }) => {
  api.answer("GET /settings", { json: PROVIDER_SET_ROWS });
  await openSettings(page);
  await page.getByRole("heading", { name: "Provider sets", level: 3 }).click();
  const spare = page.getByRole("group", { name: "provider set spare" });
  await spare.getByRole("button", { name: "stop changing framesProcessing.framesPerCall in spare" }).click();
  await expect(spare).toContainText("It changes nothing yet");
  await expect(page.getByRole("button", { name: "Save settings (1)" })).toBeVisible();
});

// A setting with no value of its own showed "(not set)", "(empty)" or an empty "default:" while a run used a value. Each
// such line now says what a run uses, and a built-in value carries no "code" or "default" mark
for (const size of SCREEN_WIDTHS) {
  test(`a setting with no value of its own says what a run uses, never an empty line, at ${size.width} px`,
    async ({ page, api }) => {
      api.answer("GET /settings", { json: PROVIDER_SET_ROWS });
      await page.setViewportSize(size);
      await openSettings(page);
      await openCard(page, "Audio", "Speech to text");
      const language = page.getByRole("textbox", { name: "audio.language" });
      await expect(language).toHaveAttribute("placeholder", "found from the recording");
      await expectInsideScreen(page, language);
      const row = language.locator("xpath=ancestor::div[contains(@class,'setting-row')]");
      await expect(row.getByText("code", { exact: true })).toHaveCount(0);
      await expect(row.getByText("default", { exact: true })).toHaveCount(0);

      await page.getByRole("heading", { name: "Model servers", exact: true, level: 3 }).click();
      const frame = page.getByRole("group", { name: "model frame", exact: true });
      await frame.locator("summary").first().click();
      // a library model names the provider sets and jobs that use it, never a job of its own, and carries no "changed" badge
      await expect(frame.getByText("Used by: default (Frames processing)")).toBeVisible();
      await expect(frame.getByText("Reads the frames")).toHaveCount(0);
      await expect(frame.getByText("changed", { exact: true })).toHaveCount(0);
      const body = await page.locator("body").innerText();
      expect(body).not.toMatch(/\(not set\)|\(empty\)|not set: uses its default/);
    });
}
