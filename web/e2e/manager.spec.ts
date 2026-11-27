// The Manager tab's tools over many runs: Download (a zip of the ticked runs' documents, filtered by tag) and Rename
// (runs shown under their AI name).
import { expect, test } from "./fixtures/mockApi";
import type { Page } from "@playwright/test";
import { expectInsideScreen, SCREEN_WIDTHS } from "./fixtures/layout";

async function openTool(page: Page, tool: "Download" | "Rename") {
  await page.goto("/");
  await page.getByRole("tab", { name: "Manager" }).click();
  await page.getByRole("navigation", { name: "manager tools" }).getByRole("button", { name: tool }).click();
  await expect(page.getByRole("heading", { name: tool, level: 2 })).toBeVisible();
}

test("the Manager tab lists its tools", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("tab", { name: "Manager" }).click();

  const tools = page.getByRole("navigation", { name: "manager tools" });
  await expect(tools.getByRole("button", { name: "Download" })).not.toHaveAttribute("aria-current");
  await expect(tools.getByRole("button", { name: "Rename" })).toBeVisible();
  await tools.getByRole("button", { name: "Download" }).click();
  await expect(tools.getByRole("button", { name: "Download" })).toHaveAttribute("aria-current", "true");
});

for (const size of SCREEN_WIDTHS) {
  test(`Compress videos ticks all, none or the inverse and compresses the ticked ones, on a ${size.width} px screen`, async ({ page, api }) => {
    await page.setViewportSize(size);
    await page.goto("/");
    await page.getByRole("tab", { name: "Manager" }).click();
    await page.getByRole("navigation", { name: "manager tools" }).getByRole("button", { name: "Compress videos" }).click();
    await expect(page.getByRole("heading", { name: "Compress videos", level: 2 })).toBeVisible();
    // a compressed video stays listed and cannot be ticked
    await expect(page.getByRole("checkbox", { name: "tick Jonas onboarding" })).toBeDisabled();
    await page.getByRole("button", { name: "All 2" }).click();
    await page.getByRole("checkbox", { name: "tick Gateway review" }).click();
    await page.getByRole("button", { name: "Invert" }).click();
    await expect(page.getByRole("checkbox", { name: "tick Gateway review" })).toBeChecked();
    await expect(page.getByRole("checkbox", { name: /tick AcmeSync kickoff/ })).not.toBeChecked();
    await page.getByRole("button", { name: "None" }).click();
    await expect(page.getByRole("button", { name: "Compress", exact: true })).toBeDisabled();
    await page.getByRole("button", { name: "All 2" }).click();
    for (const part of [page.getByRole("button", { name: "Invert" }), page.getByText(/AcmeSync kickoff with a long name/),
      page.getByText("compressed · 300 MB → 90 MB")]) {
      await expectInsideScreen(page, part);
    }
    await page.getByRole("button", { name: /Compress 2/ }).click();
    await expect(page.getByText("Compressing 2 videos, one after another")).toBeVisible();
    expect(api.sentTo("POST /videos/compression")[0].body).toEqual({ runs: [{ root: "output", name: "gateway-review" },
      { root: "output", name: "acmesync-kickoff" }] });
  });
}

test("Download lists the runs with a document and narrows them by tag", async ({ page }) => {
  await openTool(page, "Download");

  // one part to download is no choice, so no box offers it
  await expect(page.getByRole("checkbox", { name: "Published document and its figures" })).toHaveCount(0);
  await expect(page.getByText("2 runs with a document")).toBeVisible();
  await expect(page.getByRole("button", { name: "Tick runs to download" })).toBeDisabled();

  // the tags are chosen in a box that suggests them as they are typed
  await page.getByRole("combobox", { name: "filter by tag" }).fill("gateway");
  await page.getByRole("combobox", { name: "filter by tag" }).press("Enter");
  await expect(page.getByText("1 run with a document and every chosen tag")).toBeVisible();
  await expect(page.getByRole("checkbox", { name: /acmesync-retro/ })).toHaveCount(0);
});

test("Download saves the ticked runs as one zip", async ({ page, api }) => {
  await openTool(page, "Download");

  await page.getByRole("checkbox", { name: /gateway-review/ }).check();
  const saved = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download 1" }).click();
  expect((await saved).suggestedFilename()).toBe("documents.zip");

  await expect(page.getByText("Downloaded 1 run")).toBeVisible();
  expect(api.sentTo("POST /download/documents")[0].body).toEqual({
    runs: [{ root: "output", name: "gateway-review" }], parts: ["document"], version: "",
  });
});

test("Rename names a run still writing as renamed when its run ends, never as an error defect", async ({ page, api }) => {
  // A run that was writing was refused in a red message and never renamed; now the server renames it when the run
  // ends, and the page says so in a message that is no error
  api.answer("POST /runs/display-names", { json: { records: [{ name: "gateway-review", outcome: "renamed when its run ends" }] } });
  await openTool(page, "Rename");
  await page.getByRole("checkbox", { name: /gateway-review/ }).check();
  await page.getByRole("button", { name: "Use AI name for 1" }).click();
  const message = page.locator(".toast", { hasText: "gateway-review: renamed when its run ends" });
  await expect(message).toBeVisible();
  await expect(message).not.toHaveClass(/error/);
});

test("Rename shows the runs with an AI name and names the ticked ones by it", async ({ page, api }) => {
  await openTool(page, "Rename");

  // two tabs: the runs under their file name, and those under their AI name
  await expect(page.getByRole("tab", { name: "File name (2)" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "AI name (0)" })).toBeVisible();
  await expect(page.getByText("AI name: Gateway rollout plan")).toBeVisible();
  // each tab offers its own action alone: the file-name tab names runs by their AI name
  await expect(page.getByRole("button", { name: "Use file name" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Use AI name" })).toBeDisabled();

  await page.getByRole("checkbox", { name: /gateway-review/ }).check();
  await page.getByRole("button", { name: "Use AI name for 1" }).click();
  await expect(page.getByText("1 of 1 run renamed to their AI name")).toBeVisible();
  expect(api.sentTo("POST /runs/display-names")[0].body).toEqual({
    runs: [{ root: "output", name: "gateway-review" }], is_by_topic: true,
  });
});
