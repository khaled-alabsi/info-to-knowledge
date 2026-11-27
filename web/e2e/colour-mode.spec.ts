// Light and dark colour mode: the page follows the system's setting (it has no switch of its own), so each mode is
// checked by the colours a reader sees on the page's background and text.
import { expect, test } from "./fixtures/mockApi";

const LIGHT_BACKGROUND = "rgb(244, 245, 247)";
const DARK_BACKGROUND = "rgb(13, 16, 21)";

async function readColours(page: import("@playwright/test").Page) {
  return page.evaluate(() => {
    const style = getComputedStyle(document.body);
    return { background: style.backgroundColor, text: style.color };
  });
}

test.describe("light mode", () => {
  test.use({ colorScheme: "light" });

  test("draws a light page with dark text", async ({ page, api }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Nothing open" })).toBeVisible();

    const colours = await readColours(page);
    expect(colours.background).toBe(LIGHT_BACKGROUND);
    expect(colours.text).toBe("rgb(22, 26, 33)");
    expect(api.unanswered).toEqual([]);
  });
});

test.describe("dark mode", () => {
  test.use({ colorScheme: "dark" });

  test("draws a dark page with light text", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Nothing open" })).toBeVisible();

    const colours = await readColours(page);
    expect(colours.background).toBe(DARK_BACKGROUND);
    expect(colours.text).toBe("rgb(230, 233, 238)");
  });

  test("keeps the inbox readable in dark mode", async ({ page }) => {
    await page.goto("/");

    const row = page.getByText("AcmeSync kickoff.mp4");
    await expect(row).toBeVisible();
    const rowColour = await row.evaluate((element) => getComputedStyle(element).color);
    expect(rowColour).not.toBe(DARK_BACKGROUND);
  });
});
