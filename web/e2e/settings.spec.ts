// The Settings tab: settings grouped by section, a change shown as a draft until the top bar's Save settings writes
// it, a search that opens the groups holding a match, and a profile's own file.
import { expect, test } from "./fixtures/mockApi";
import type { Page } from "@playwright/test";
import { SETTINGS } from "./fixtures/data";
import { expectInsideScreen, SCREEN_WIDTHS } from "./fixtures/layout";

async function openSettings(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Settings", level: 2 })).toBeVisible();
}

test("lists every setting under its section, each group closed until opened", async ({ page }) => {
  await openSettings(page);

  await expect(page.getByRole("heading", { name: "Frames processing", level: 3 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Review", level: 3 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Frames", exact: true, level: 3 })).toBeVisible();
  const framesPerCall = page.getByRole("spinbutton", { name: "framesProcessing.framesPerCall" });
  await expect(framesPerCall).toBeHidden();

  await page.getByRole("heading", { name: "Frames processing" }).click();
  await expect(framesPerCall).toHaveValue("7");
  await expect(page.getByText("How many consecutive frames one call reads.")).toBeVisible();
  await expect(page.getByText("playbook.local.json").first()).toBeVisible();
});

test("a change is a draft until Save settings writes it", async ({ page, api }) => {
  await openSettings(page);
  await page.getByRole("heading", { name: "Frames processing" }).click();

  const framesPerCall = page.getByRole("spinbutton", { name: "framesProcessing.framesPerCall" });
  await framesPerCall.fill("9");
  await expect(page.getByText("1 · 1 changed")).toBeVisible();
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
  await page.getByRole("heading", { name: "Review" }).click();

  const reviewSwitch = page.getByRole("checkbox", { name: "review.isOn" });
  await reviewSwitch.uncheck();
  await expect(page.getByRole("button", { name: "Save settings (1)" })).toBeVisible();
  await reviewSwitch.check();
  await expect(page.getByRole("button", { name: /Save settings/ })).toHaveCount(0);
});

test("a refused setting stays a draft and says why", async ({ page, api }) => {
  api.answer("PUT /settings", { json: { exit_code: 1, output: "frames.mode: not one of change, interval" } });
  await openSettings(page);
  await page.getByRole("heading", { name: "Frames", exact: true }).click();

  await page.getByRole("button", { name: "frames.mode: change" }).click();
  await page.getByRole("menuitemradio", { name: "interval" }).click();
  await page.getByRole("button", { name: "Save settings (1)" }).click();
  await expect(page.getByText("frames.mode: not one of change, interval")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save settings (1)" })).toBeVisible();
});

test("the search opens each group holding a match and hides the rest", async ({ page }) => {
  await openSettings(page);

  await page.getByRole("searchbox").fill("consecutive");
  await expect(page.getByRole("spinbutton", { name: "framesProcessing.framesPerCall" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Review", level: 3 })).toHaveCount(0);
});

test("choosing a profile reads its file and drops the drafts typed for the default settings", async ({ page, api }) => {
  await openSettings(page);
  await page.getByRole("heading", { name: "Frames processing" }).click();
  await page.getByRole("spinbutton", { name: "framesProcessing.framesPerCall" }).fill("9");
  await expect(page.getByRole("button", { name: "Save settings (1)" })).toBeVisible();

  const readsBefore = api.sentTo("GET /settings").length;

  await page.getByRole("button", { name: "Edit: default settings" }).click();
  await page.getByRole("menuitemradio", { name: "profile: strict" }).click();
  // the unsaved change is named before it is dropped, the warning inside the screen at every width
  const warning = page.getByRole("alert");
  await expect(warning).toContainText("You have 1 unsaved change to the default settings");
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await expectInsideScreen(page, warning.getByRole("button", { name: "Discard the changes and switch" }));
  }
  await warning.getByRole("button", { name: "Discard the changes and switch" }).click();
  await expect(page.getByRole("button", { name: /Save settings/ })).toHaveCount(0);
  await expect(page.getByText(/Profile strict sets/)).toBeVisible();
  await expect.poll(() => api.sentTo("GET /settings").length).toBeGreaterThan(readsBefore);
});

test("a model's address is chosen from the connections the profiles name, or typed; + Add model copies one; all inside the screen", async ({ page, api }) => {
  api.answer("GET /settings", { json: [...SETTINGS,
    { key: "models.frame.spec", path: ["models", "frame", "spec"], value: "lmstudio:acme-vision-35b", kind: "text",
      source: "playbook.local.json", comment: "", is_open_choice: true, choices: [], choice_options: [
        { value: "lmstudio:acme-vision-35b", label: "acme-vision-35b", hint: "", group: "LM Studio at http://other-mac:1234/v1" },
        { value: "lmstudio:acme-vision-4b-with-a-very-long-name-from-the-host",
          label: "acme-vision-4b-with-a-very-long-name-from-the-host", hint: "", group: "LM Studio at http://localhost:1234/v1" }] },
    { key: "models.frame.baseUrl", path: ["models", "frame", "baseUrl"], value: null, kind: "text", source: "code",
      comment: "The host's address", is_open_choice: true, choices: [], choice_options: [
        { value: "", label: "(empty)", hint: "its default", group: "" },
        { value: "http://localhost:1234/v1", label: "http://localhost:1234/v1", hint: "LM Studio on this machine", group: "" },
        { value: "http://other-mac:1234/v1", label: "http://other-mac:1234/v1", hint: "profile other", group: "" }] }] });
  await openSettings(page);
  await page.getByRole("heading", { name: "Models", exact: true, level: 3 }).click();

  const spec = page.getByRole("button", { name: /^models\.frame\.spec: / });
  await spec.click();
  const specMenu = page.getByRole("menu", { name: "models.frame.spec" });
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await expectInsideScreen(page, specMenu);
  }
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "models.frame.baseUrl: (empty)" }).click();
  await page.getByRole("menuitemradio", { name: /other-mac/ }).click();
  await expect(page.getByRole("button", { name: "models.frame.baseUrl: http://other-mac:1234/v1" })).toBeVisible();
  await page.getByRole("button", { name: "+ Add model" }).click();
  await page.getByRole("textbox", { name: "New model's name" }).fill("frame-this-mac");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "models.frame-this-mac" })).toBeVisible();
  await page.getByRole("button", { name: "+ Add model" }).click();
  await page.getByRole("button", { name: /^models\.frame\.baseUrl: / }).click();
  await page.getByRole("menuitem", { name: /Other…/ }).click();
  const typed = page.getByRole("textbox", { name: "models.frame.baseUrl, your own value" });
  await typed.fill("http://third-mac:1234/v1");

  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    for (const part of [spec, typed, page.getByRole("textbox", { name: "New model's name" }),
      page.getByRole("button", { name: "Cancel" })]) {
      await expectInsideScreen(page, part);
    }
  }

  await page.getByRole("button", { name: "Save settings (2)" }).click();
  await expect.poll(() => api.sentTo("PUT /settings").map((call) => call.body)).toEqual([
    { key: "models.frame.baseUrl", new_value: "http://third-mac:1234/v1", profile: "", key_parts: ["models", "frame", "baseUrl"] },
    { key: "models.frame-this-mac", new_value: { spec: "lmstudio:acme-vision-35b" }, profile: "",
      key_parts: ["models", "frame-this-mac"] }]);
});

test("each LLM pass's extra wishes have their own box in the PDF section, inside the screen at every width", async ({ page, api }) => {
  api.answer("GET /settings", { json: [...SETTINGS,
    { key: "pdf.cleanupWishes", value: "remove every Jira link", kind: "long-text", source: "playbook.local.json",
      comment: "The extra wishes of phase 7's LLM clean up." },
    { key: "pdf.rewriteWishes", value: "", kind: "long-text", source: "code",
      comment: "The extra wishes of phase 7's LLM rewrite." }] });
  await openSettings(page);
  await page.getByRole("heading", { name: "PDF", exact: true, level: 3 }).click();

  const cleanup = page.getByRole("textbox", { name: "pdf.cleanupWishes" });
  await expect(cleanup).toHaveValue("remove every Jira link");
  await expect(cleanup).toHaveJSProperty("tagName", "TEXTAREA");
  await expect(page.getByRole("textbox", { name: "pdf.rewriteWishes" })).toHaveJSProperty("tagName", "TEXTAREA");
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await expectInsideScreen(page, cleanup);
  }
});
