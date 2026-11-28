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

// Opens every server card and provider set card shown: each stands closed until opened, like every group of settings
async function openCards(page: Page) {
  await page.locator("details.server-card, details.provider-set").evaluateAll((cards) =>
    cards.forEach((card) => { (card as HTMLDetailsElement).open = true; }));
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
  const framesPerCall = page.getByRole("textbox", { name: "Frames per call" });
  await expect(framesPerCall).toBeHidden();

  await page.getByRole("heading", { name: "Frames", exact: true, level: 3 }).click();
  // an opened section shows its cards' titles alone
  await expect(page.getByRole("heading", { name: "Frames processing", exact: true, level: 4 })).toBeVisible();
  await expect(framesPerCall).toBeHidden();
  await page.getByRole("heading", { name: "Frames processing", exact: true, level: 4 }).click();
  await expect(framesPerCall).toHaveValue("7");
  // the help text comes from the page's own labels, not the settings file's comment
  await expect(page.getByText("How many consecutive frames the model reads in one call; 1 means one call for each frame.")).toBeVisible();
  // a saved value carries no "changed" mark, whichever file set it
  await expect(page.getByText("changed", { exact: true })).toHaveCount(0);
});

test("a change is a draft until Save settings writes it", async ({ page, api }) => {
  await openSettings(page);
  await openCard(page, "Frames", "Frames processing");

  const framesPerCall = page.getByRole("textbox", { name: "Frames per call" });
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
  await openCard(page, "Review and quality", "Checking the finished document");

  const reviewSwitch = page.getByRole("checkbox", { name: "Run the review" });
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
  await page.getByRole("button", { name: "Capture mode: When the screen changes" }).click();
  await page.getByRole("menuitemradio", { name: "At a fixed rate" }).click();
  await page.getByRole("button", { name: "Save settings (1)" }).click();
  await expect(page.getByText("frames.mode: not one of change, interval")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save settings (1)" })).toBeVisible();
});

test("the search opens each group holding a match and hides the rest", async ({ page }) => {
  await openSettings(page);

  await page.getByRole("searchbox").fill("consecutive");
  await expect(page.getByRole("textbox", { name: "Frames per call" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Review and quality", level: 3 })).toHaveCount(0);
});

test("choosing a profile reads its file and drops the drafts typed for the default settings", async ({ page, api }) => {
  await openSettings(page);
  await openCard(page, "Frames", "Frames processing");
  await page.getByRole("textbox", { name: "Frames per call" }).fill("9");
  await expect(page.getByRole("button", { name: "Save settings (1)" })).toBeVisible();

  const readsBefore = api.sentTo("GET /settings").length;

  await page.getByRole("button", { name: "Edit: default settings" }).click();
  await page.getByRole("menuitemradio", { name: /^fast/ }).click();
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
    { key: "pdf.rewriteWishes", value: "", kind: "long-text", source: "playbook.local.json",
      comment: "The extra wishes of phase 7's LLM rewrite." }] });
  await openSettings(page);
  await openCard(page, "Document", "PDFs");

  const cleanup = page.getByRole("textbox", { name: "Extra instructions for the clean up" });
  await expect(cleanup).toHaveValue("remove every Jira link");
  await expect(cleanup).toHaveJSProperty("tagName", "TEXTAREA");
  await expect(page.getByRole("textbox", { name: "Extra instructions for the rewrite" })).toHaveJSProperty("tagName", "TEXTAREA");
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
  await page.getByRole("heading", { name: "Model servers", exact: true, level: 3 }).click(); await openCards(page);

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
      await page.getByRole("heading", { name: "Provider sets", level: 3 }).click(); await openCards(page);

      // the default set shows each job with the library model it runs and where; another set shows what it leaves as the default's
      const fallback = page.getByRole("group", { name: "provider set default" });
      await expectInsideScreen(page, fallback);
      // the server is named once, in the choice, never again in a "Runs on" line under it
      await expect(fallback.getByText("Runs on lmstudio", { exact: false })).toHaveCount(0);
      // the fallback names the model it picks, never a word like "usual" (the owner: "Usual word make no sense")
      await expect(fallback.getByRole("button", { name: "default: Frames processing: acme-vision-35b on lmstudio" })).toBeVisible();
      await expect(fallback.getByText(/usual/i)).toHaveCount(0);
      await expectInsideScreen(page, fallback.getByText("Used by runs that pick no set"));
      const spare = page.getByRole("group", { name: "provider set spare" });
      await expectInsideScreen(page, spare);
      // a set has no Machine box, and it shows no job it leaves as the default's: its menu adds one
      await expect(spare.getByRole("button", { name: /^Machine of/ })).toHaveCount(0);
      await expect(spare.getByRole("button", { name: "spare: Frames processing" })).toHaveCount(0);
      await expect(spare.getByRole("button", { name: "remove provider set spare" })).toBeVisible();
      await expect(spare.getByText("default: 7")).toBeVisible();

      // a job is overridden from the set's menu, then linked to a library model from the list of the library's names
      const override = spare.getByRole("button", { name: "Override a job" });
      await scrollIntoViewSettled(page, override);
      await override.click();
      const overrideMenu = page.getByRole("menu");
      await expectInsideScreen(page, overrideMenu);
      await overrideMenu.getByRole("menuitem", { name: /^Frames processing/ }).click();
      const link = spare.getByRole("button", { name: /^spare: Frames processing/ });
      await expectInsideScreen(page, link);
      await scrollIntoViewSettled(page, link);
      await link.click();
      const menu = page.getByRole("menu", { name: "spare: Frames processing" });
      await expectInsideScreen(page, menu);
      await menu.getByRole("menuitemradio", { name: /^frame-gpu/ }).click();
      await expect(spare.getByText("Runs on gpu-box")).toBeVisible();

      const value = spare.getByRole("textbox", { name: "spare: Frames per call" });
      await value.fill("6");
      await page.getByRole("button", { name: "Save settings (1)" }).click();
      const sent = api.sentTo("PUT /settings");
      await expect.poll(() => sent.length).toBe(1);
      expect(sent[0].body).toMatchObject({ key: "providerSets", new_value: { spare: {
        phaseModels: { frames_processing: "frame-gpu" }, framesProcessing: { framesPerCall: 6 } } } });
    });
}

for (const size of SCREEN_WIDTHS) {
  test(`every server card and provider set card starts closed, its title alone shown, at ${size.width} px defect`,
    async ({ page, api }) => {
      // Defect as shown: a section opened showed every server and every set open, a long page of fields.
      // The fix: each card starts closed like every other group of settings, and opens by its title
      api.answer("GET /settings", { json: PROVIDER_SET_ROWS });
      await page.setViewportSize(size);
      await openSettings(page);
      await page.getByRole("heading", { name: "Model servers", exact: true, level: 3 }).click();
      await page.getByRole("heading", { name: "Provider sets", level: 3 }).click();
      for (const card of await page.locator("details.server-card, details.provider-set").all()) {
        await expect(card).not.toHaveAttribute("open", "");
        await expectInsideScreen(page, card.locator(":scope > summary"));
      }      const spare = page.getByRole("group", { name: "provider set spare" });
      await expect(spare.getByRole("button", { name: "remove provider set spare" })).toBeHidden();
      await spare.locator(":scope > summary").click();
      await expect(spare.getByRole("button", { name: "remove provider set spare" })).toBeVisible();
    });
}

test("Copy on a provider set card creates a new set from it and asks for its name", async ({ page, api }) => {
  api.answer("GET /settings", { json: PROVIDER_SET_ROWS });
  await openSettings(page);
  await page.getByRole("heading", { name: "Provider sets", level: 3 }).click(); await openCards(page);
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

  await page.getByRole("heading", { name: "Model servers", exact: true, level: 3 }).click(); await openCards(page);
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
  await page.getByRole("heading", { name: "Model servers", exact: true, level: 3 }).click(); await openCards(page);
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
      await page.getByRole("heading", { name: "Model servers", exact: true, level: 3 }).click(); await openCards(page);
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

      await page.getByRole("heading", { name: "Provider sets", level: 3 }).click(); await openCards(page);
      const spare = page.getByRole("group", { name: "provider set spare" });
      const overrideButton = spare.getByRole("button", { name: "Override a job" });
      await scrollIntoViewSettled(page, overrideButton);
      await overrideButton.click();
      await page.getByRole("menuitem", { name: /^Speech to text/ }).click();
      const speech = spare.getByRole("button", { name: /^spare: Speech to text/ });
      await scrollIntoViewSettled(page, speech);
      await speech.click();
      const menu = page.getByRole("menu", { name: "spare: Speech to text" });
      await expectInsideScreen(page, menu);
      await expect(menu.getByRole("menuitemradio", { name: /^whisper-gpu/ })).toBeVisible();
      // a model on the chat-only server is not offered to speech to text
      await expect(menu.getByRole("menuitemradio", { name: /^frame(?!-)/ })).toHaveCount(0);
      await menu.getByRole("menuitemradio", { name: /^whisper-gpu/ }).click();
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
      await openCards(page);

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
      const base = page.getByRole("group", { name: "provider set default" });
      await expectInsideScreen(page, base);
      const preparing = base.getByRole("button", { name: /^default: Frames preparing/ });
      await scrollIntoViewSettled(page, preparing);
      await preparing.click();
      const preparingMenu = page.getByRole("menu", { name: "default: Frames preparing" });
      await expect(preparingMenu.getByRole("menuitemradio", { name: /^small-filter/ })).toBeVisible();
      await expect(preparingMenu.getByRole("menuitemradio", { name: /^frame(?!-)/ })).toHaveCount(0);
      // an option names its server plainly, never an address
      await expect(preparingMenu).not.toContainText(/localhost|http|\.local/);
      await page.keyboard.press("Escape");
      const processing = base.getByRole("button", { name: /^default: Frames processing/ });
      await scrollIntoViewSettled(page, processing);
      await processing.click();
      const processingMenu = page.getByRole("menu", { name: "default: Frames processing" });
      await expect(processingMenu.getByRole("menuitemradio", { name: /^frame(?!-)/ })).toBeVisible();
      await expect(processingMenu.getByRole("menuitemradio", { name: /^small-filter/ })).toHaveCount(0);
      await page.keyboard.press("Escape");
      // the Decision filter offers the filter model of a chat server, not only the program's own way
      const decision = base.getByRole("button", { name: /^default: Decision filter/ });
      await scrollIntoViewSettled(page, decision);
      await decision.click();
      const decisionMenu = page.getByRole("menu", { name: "default: Decision filter" });
      await expect(decisionMenu.getByRole("menuitemradio", { name: /^small-filter/ })).toBeVisible();
      await expectInsideScreen(page, decisionMenu.getByRole("menuitemradio", { name: /^small-filter/ }));
      await expect(decisionMenu.getByRole("menuitemradio", { name: /^frame(?!-)/ })).toHaveCount(0);
      await page.keyboard.press("Escape");

      // every job is its own card in the default set; no Machine box and no fast workflow model menus
      await expect(base.getByRole("button", { name: /^Machine of/ })).toHaveCount(0);
      await expect(page.getByText(/Fast workflow|Frame describer|Document writer/)).toHaveCount(0);
      for (const job of ["Speech to text", "Scanned page reader", "Speaker detection", "Frame vectors", "Decision filter"]) {
        await expect(base.locator(".job-link").filter({ hasText: job })).toHaveCount(1);
      }
      expect(await base.locator(".job-link").count()).toBe(9);
      // no backtick and no quoted-empty pair anywhere on the page
      const text = await page.locator(".settings-view").innerText();
      expect(text).not.toContain("`");
      expect(text).not.toContain('""');
    });
}

// A number box showed 0.95 as "0,95" in a German browser, beside boxes that show a dot. Every number now shows a dot
test.describe("in a German browser", () => {
  test.use({ locale: "de-DE" });
  for (const size of SCREEN_WIDTHS) {
    test(`a decimal setting shows a dot, at ${size.width} px`, async ({ page, api }) => {
      api.answer("GET /settings", { json: [...SETTINGS, { key: "frames.decisionFilterCut", path: ["frames", "decisionFilterCut"],
        value: 0.95, kind: "number", source: "local", comment: "" }] });
      await page.setViewportSize(size);
      await openSettings(page);
      await page.getByRole("searchbox").fill("cut-off");
      const box = page.getByRole("textbox", { name: "Decision filter cut-off (0 to 1)" });
      await expect(box).toHaveValue("0.95");
      await expectInsideScreen(page, box);
      expect(await page.locator(".settings-view").innerText()).not.toContain("0,95");
    });
  }
});

test("a set's value set back to the default leaves the set changing nothing", async ({ page, api }) => {
  api.answer("GET /settings", { json: PROVIDER_SET_ROWS });
  await openSettings(page);
  await page.getByRole("heading", { name: "Provider sets", level: 3 }).click(); await openCards(page);
  const spare = page.getByRole("group", { name: "provider set spare" });
  await spare.getByRole("button", { name: "stop changing Frames per call in spare" }).click();
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
      // a setting nobody changed is not on the page: a search finds it
      await page.getByRole("searchbox").fill("Spoken language");
      const language = page.getByRole("textbox", { name: "Spoken language" });
      // the box holds the short value and the note stands under it, so nothing is cut off on a phone
      await expect(language).toHaveAttribute("placeholder", "automatic");
      await expectInsideScreen(page, language);
      const row = language.locator("xpath=ancestor::div[contains(@class,'setting-row')]");
      await expect(row.getByText("found from the recording")).toBeVisible();
      await expectInsideScreen(page, row.getByText("found from the recording"));
      await expect(row.getByText("code", { exact: true })).toHaveCount(0);
      await expect(row.getByText("default", { exact: true })).toHaveCount(0);
      await page.getByRole("searchbox").fill("");

      await page.getByRole("heading", { name: "Model servers", exact: true, level: 3 }).click(); await openCards(page);
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

// The critic's faults on a phone: a top bar of two rows, a server with no models as an empty card, the "+ Add model" button off
// the line of its cards, a model's options as a table of typed names, and a menu cut off at the screen's edge
const CRITIC_ROWS = [...PROVIDER_SET_ROWS.filter((row) => row.key !== "hosts"),
  { key: "hosts", kind: "json", source: "playbook.local.json", comment: "", value: {
    lmstudio: { url: "http://localhost:1234/v1" }, "gpu-box": { url: "http://gpu-box.local:8090/v1" },
    "spare-box": { url: "http://spare-box.local:8090/v1", apiKeyEnv: "" } } },
  { key: "models.frame.sampling", path: ["models", "frame", "sampling"], value: { top_k: 20 }, kind: "json",
    source: "playbook.local.json", comment: "" }];

for (const size of SCREEN_WIDTHS) {
  test(`the top bar, an empty server, the add button and a model's options stay tidy and inside the screen, at ${size.width} px defect`,
    async ({ page, api }) => {
      // Fixed: the top bar held two rows on a phone, a server without models was an empty card, "+ Add model" stood off the
      // cards' line, a model's options were typed names in a table and the menus had no icon. All inside the screen now.
      api.answer("GET /settings", { json: CRITIC_ROWS });
      await page.setViewportSize(size);
      await openSettings(page);
      if (size.width <= 640) {
        const bar = await page.locator(".topbar").boundingBox();
        expect(bar?.height ?? 999, "the top bar is one row on a phone").toBeLessThan(70);
      }
      await expectInsideScreen(page, page.getByRole("button", { name: "Close settings" }));

      // the edit menu has an icon on every row and stays on the screen
      await page.getByRole("button", { name: "Edit: default settings" }).click();
      const editMenu = page.getByRole("menu", { name: "Edit" });
      await expectInsideScreen(page, editMenu);
      expect(await editMenu.locator("button[role='menuitemradio'] > svg").count()).toBeGreaterThan(0);
      await page.keyboard.press("Escape");

      await page.getByRole("heading", { name: "Model servers", exact: true, level: 3 }).click(); await openCards(page);
      const spare = page.getByRole("group", { name: "server spare-box" });
      await expect(spare.getByText("No models yet.")).toBeVisible();
      await expectInsideScreen(page, spare.getByText("No models yet."));
      await expectInsideScreen(page, spare.getByRole("textbox", { name: "login key name of spare-box" }));

      // "+ Add model" stands on the left edge of the model cards above it
      const local = page.getByRole("group", { name: "server lmstudio" });
      const frame = local.getByRole("group", { name: "model frame", exact: true });
      const add = local.getByRole("button", { name: "add model to lmstudio" });
      const cardBox = await frame.boundingBox();
      const addBox = await add.boundingBox();
      expect(addBox && cardBox && Math.abs(addBox.x - cardBox.x)).toBeLessThanOrEqual(1);

      // a model's options are labelled fields with an Add an option menu, never a table of typed names
      await frame.locator("summary").first().click();
      await frame.getByText("Advanced", { exact: true }).click();
      const options = frame.getByRole("textbox", { name: /Number of word choices/ }).or(frame.getByRole("spinbutton", { name: /Number of word choices/ }));
      await expectInsideScreen(page, options.first());
      await expect(frame.getByRole("columnheader")).toHaveCount(0);
      const addOption = frame.getByRole("button", { name: "Add an option" });
      await scrollIntoViewSettled(page, addOption);
      await addOption.click();
      await expectInsideScreen(page, page.getByRole("menu"));
      await page.keyboard.press("Escape");
    });
}


// A setting nobody changed is not on the page until a search finds it; a value with fixed choices is a menu, never a typing box
const PRECISION_ROW = { key: "audio.fasterWhisper.computeType", path: ["audio", "fasterWhisper", "computeType"], value: "int8", kind: "text",
  source: "code", comment: "" };

for (const size of SCREEN_WIDTHS) {
  test(`a setting nobody changed is found by a search and its fixed values are a menu, at ${size.width} px defect`,
    async ({ page, api }) => {
      // Fixed: every setting was listed in six extra sections and fixed values were typing boxes. Now only a search shows an
      // unchanged setting, and its fixed values are a menu named in plain words.
      api.answer("GET /settings", { json: [...SETTINGS, PRECISION_ROW] });
      await page.setViewportSize(size);
      await openSettings(page);
      await expect(page.getByRole("heading", { name: "Audio", exact: true, level: 3 })).toHaveCount(0);
      await expect(page.getByRole("combobox", { name: "Standard engine precision" })).toHaveCount(0);

      await page.getByRole("searchbox").fill("Standard engine precision");
      const choice = page.getByRole("button", { name: /^Standard engine precision/ });
      await expectInsideScreen(page, choice);
      await expect(page.getByRole("textbox", { name: "Standard engine precision" })).toHaveCount(0);
      await scrollIntoViewSettled(page, choice);
      await choice.click();
      await expectInsideScreen(page, page.getByRole("menu"));
      expect(await page.getByRole("menuitemradio").count()).toBeGreaterThan(1);
      await page.keyboard.press("Escape");
    });
}

