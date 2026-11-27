// The screen widths every page change is looked at on (AGENTS.md: 390, 768, 1100 and 1440 px), and the check that a
// part shown lies inside the screen: a menu's panel ran past the right edge of a middle-width screen, its options and
// its button cut off, while its test ran at one width.
import { expect } from "@playwright/test";
import type { Locator, Page } from "@playwright/test";

export const SCREEN_WIDTHS = [
  { width: 390, height: 844 },
  { width: 768, height: 1024 },
  { width: 1100, height: 800 },
  { width: 1440, height: 900 },
];

// `part` lies inside the screen, and nothing in it is wider than it (no sideways scroll inside it)
export async function expectInsideScreen(page: Page, part: Locator) {
  const viewport = page.viewportSize();
  const box = await part.boundingBox();
  expect(box, "the part is shown").not.toBeNull();
  if (!box || !viewport) return;
  expect(box.x, "its left edge is on the screen").toBeGreaterThanOrEqual(0);
  expect(box.x + box.width, `its right edge is on a ${viewport.width} px screen`).toBeLessThanOrEqual(viewport.width);
  const overflow = await part.evaluate((element) => element.scrollWidth - element.clientWidth);
  expect(overflow, "nothing inside it runs past its right edge").toBeLessThanOrEqual(1);
}
