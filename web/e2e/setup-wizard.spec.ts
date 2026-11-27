// The setup wizard: hidden while everything a run needs is there, offered from the top bar when something is
// missing, and opened by itself when no model is configured.
import { expect, test } from "./fixtures/mockApi";
import { SETUP_NO_MODELS, SETUP_READY, SETUP_TOOL_MISSING } from "./fixtures/data";
import { expectInsideScreen, SCREEN_WIDTHS } from "./fixtures/layout";
import type { Page } from "@playwright/test";

// A missing part marks the top bar's gear; the Settings page it opens offers Setup needed, which opens the wizard
async function openSetupWizard(page: Page) {
  await page.getByRole("button", { name: "Settings: setup needed" }).click();
  await page.getByRole("button", { name: "Setup needed", exact: true }).click();
}

test("a missing part is a mark on the settings gear, inside the screen at every width", async ({ page, api }) => {
  api.answer("GET /setup", { json: SETUP_TOOL_MISSING });
  await page.goto("/");
  const gear = page.getByRole("button", { name: "Settings: setup needed" });
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await expectInsideScreen(page, gear.locator(".alert-mark"));
  }
  await expect(page.getByRole("button", { name: "Setup needed", exact: true })).toHaveCount(0);
});

test("on a short phone screen the wizard's body scrolls to its last button, its title kept", async ({ page, api }) => {
  // The owner's phone could not scroll the setup wizard: sized by a height Safari's toolbars hide, scrolled as a whole
  api.answer("GET /setup", { json: SETUP_TOOL_MISSING });
  await page.setViewportSize({ width: 390, height: 380 });
  await page.goto("/");
  await openSetupWizard(page);
  const wizard = page.getByRole("dialog");
  const body = wizard.locator(".dialog-body");
  await expect.poll(() => body.evaluate((element) => element.scrollHeight > element.clientHeight)).toBe(true);
  await body.evaluate((element) => { element.scrollTop = element.scrollHeight; });
  await expectInsideScreen(page, wizard.getByRole("button", { name: "Done" }));
  await expectInsideScreen(page, wizard.getByRole("button", { name: "close" }));
});

test("with everything set up, the top bar offers no setup", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Nothing open" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Setup needed", exact: true })).toHaveCount(0);
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("with everything set up, the Settings page still opens the wizard", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("button", { name: "Setup wizard" }).click();
  await expect(page.getByRole("dialog").getByRole("heading", { name: "Setup" })).toBeVisible();
});

test("a missing tool raises Setup needed, and the wizard walks to it", async ({ page, api }) => {
  api.answer("GET /setup", { json: SETUP_TOOL_MISSING });
  await page.goto("/");

  await openSetupWizard(page);
  const wizard = page.getByRole("dialog");
  await expect(wizard.getByRole("heading", { name: "Setup" })).toBeVisible();
  // it opens on the step with the problem, the tools; the steps before it can still be opened
  const steps = wizard.getByRole("navigation", { name: "setup steps" });
  await expect(steps.getByRole("button", { name: /3\. Tools/ })).toHaveAttribute("aria-current", "step");
  await steps.getByRole("button", { name: /1\. Model host/ }).click();
  await expect(wizard.getByRole("region", { name: "LM Studio" })).toContainText("ready");
  await wizard.getByRole("button", { name: "Next" }).click();
  await expect(wizard.getByRole("cell", { name: "lmstudio:acme-vision-35b" })).toBeVisible();
  await wizard.getByRole("button", { name: "Next" }).click();

  const ffmpeg = wizard.getByRole("region", { name: "ffmpeg" });
  await expect(ffmpeg).toContainText("missing");
  await ffmpeg.getByRole("tab", { name: "Linux" }).click();
  await expect(ffmpeg.getByText("apt install ffmpeg")).toBeVisible();
  await expect(ffmpeg.getByRole("link", { name: "Open the download page" })).toHaveAttribute("href", "https://ffmpeg.org");

  await wizard.getByRole("button", { name: "Done" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("the scanned-page reader a setting needs is a row of the Models table with its own Download, inside the screen", async ({ page, api }) => {
  api.answer("GET /setup", { json: { ...SETUP_READY, runtimes: [...SETUP_READY.runtimes,
    { key: "unlimited-ocr", name: "Unlimited-OCR-2 scanned-page reader", kind: "tool", purpose: "reads scanned pages",
      download_url: "", key_variable: "", steps: { mac: ["Press Download on the Models step"], windows: [], linux: [] },
      start_steps: [], is_needed: true, is_installed: false, is_running: null, available_models: [],
      model_id: "acme/scan-reader-8bit" }] } });
  api.answer("POST /setup/models", { json: { pid: 1 } });
  await page.goto("/");
  await openSetupWizard(page);
  const wizard = page.getByRole("dialog");
  await wizard.getByRole("navigation", { name: "setup steps" }).getByRole("button", { name: /Models/ }).click();

  const row = wizard.getByRole("row", { name: /acme\/scan-reader-8bit/ });
  await expect(row).toContainText("not downloaded");
  const download = row.getByRole("button", { name: "Download" });
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await expectInsideScreen(page, download);
  }
  await download.click();
  await expect.poll(() => api.sentTo("POST /setup/models").length).toBe(1);
});

test("with no model configured the wizard opens by itself and saves the chosen host", async ({ page, api }) => {
  api.answer("GET /setup", { json: SETUP_NO_MODELS });
  await page.goto("/");

  const wizard = page.getByRole("dialog");
  await expect(wizard.getByText("No model is configured.")).toBeVisible();
  const chooser = wizard.getByRole("region", { name: "choose a model host" });
  await expect(chooser.getByRole("radio", { name: /LM Studio/ })).toBeChecked();
  await expect(chooser.getByText("LM Studio has: acme-vision-35b")).toBeVisible();
  await chooser.getByRole("combobox", { name: "Model" }).fill("acme-vision-35b");

  await chooser.getByRole("button", { name: "Use LM Studio for every phase" }).click();
  await expect(page.getByText("LM Studio is used for every phase")).toBeVisible();
  expect(api.sentTo("POST /setup/configure")[0].body).toEqual({
    provider: "lmstudio", model_id: "acme-vision-35b", base_url: "", api_key: "",
  });

  await wizard.getByRole("button", { name: "close" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});
