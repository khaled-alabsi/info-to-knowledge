// The provider set choice: the run settings of one new video and of the inbox's ticked videos name the
// set a run is on, shown only when the settings define a set besides the default; the page sends the set with the job.
import { expect, test } from "./fixtures/mockApi";
import type { Page } from "@playwright/test";
import { expectInsideScreen, SCREEN_WIDTHS } from "./fixtures/layout";

const TWO_SETS = { default_set: "default", sets: [{ name: "default", changes: [] }, { name: "spare", changes: ["models"] }] };

// Opens the inbox's run settings and goes to the tab that holds the Provider set choice
async function openRunSettings(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "run settings" }).click();
  const dialog = page.getByRole("dialog", { name: /Run settings/ });
  await expect(dialog).toBeVisible();
  const tab = dialog.getByRole("tab", { name: /^Settings/ });
  if (await tab.count()) await tab.click();
  return dialog;
}

for (const size of SCREEN_WIDTHS) {
  test(`the inbox's run settings offer the provider sets and send the picked one with the job, at ${size.width} px`,
    async ({ page, api }) => {
      api.answer("GET /provider-sets", { json: TWO_SETS });
      await page.setViewportSize(size);
      const dialog = await openRunSettings(page);
      const choice = dialog.getByRole("combobox", { name: "Provider set" });
      await expect(choice).toBeVisible();
      await expectInsideScreen(page, choice);
      await expect(choice).toHaveValue("");
      await choice.selectOption({ label: "spare" });
      await expect(choice).toHaveValue("spare");
      await dialog.locator(".run-settings-actions .btn.primary").click();
      await expect.poll(() => api.sentTo("POST /jobs").length).toBe(1);
      expect(api.sentTo("POST /jobs")[0].body).toMatchObject({ provider_set: "spare" });
    });

  test(`a settings file with no set besides the default shows no provider set choice, at ${size.width} px`,
    async ({ page }) => {
      await page.setViewportSize(size);
      const dialog = await openRunSettings(page);
      await expect(dialog.getByRole("combobox", { name: "Provider set" })).toHaveCount(0);
    });
}
