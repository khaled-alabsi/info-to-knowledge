// A document read full screen keeps its own contents list apart from the text: a column on the left on a wide screen,
// a folded "Contents" bar above the text on a narrow one, opened by a tap and closed again by picking a heading.
import { expect, test } from "./fixtures/mockApi";
import * as data from "./fixtures/data";

const DOCUMENT_PATH = "gateway-review/gateway-review.md";
const HEADINGS = ["Gateway limits", "Order caps", "Reseller approval", "Open points", "Agenda notes", "Next steps",
  "Billing settings", "Monthly limits", "Manual review", "Follow-up"];
const DOCUMENT = ["# Gateway review", "", "## Contents", "",
  ...HEADINGS.map((heading) => `- [${heading}](#${heading.toLowerCase().replace(/ /g, "-")})`), "",
  ...HEADINGS.flatMap((heading) => [`## ${heading}`, "", `Jonas explains how the Gateway handles ${heading.toLowerCase()}.`, ""])].join("\n");

const WIDTHS = [{ width: 390, height: 844 }, { width: 768, height: 1024 }, { width: 1100, height: 800 }, { width: 1440, height: 900 }];

for (const size of WIDTHS) {
  test.describe(`at ${size.width} px`, () => {
    test.use({ viewport: size, hasTouch: size.width < 900, isMobile: size.width < 900 });

    test("the contents list takes no more than its bar on a narrow screen, a column on a wide one defect", async ({ page, api }) => {
      // On a phone the contents list stood open above the text and took half of the screen; it is now folded under a
      // "Contents" bar there, and stays a column beside the text on a wide screen
      api.answer("GET /runs/output/gateway-review/update", { json: {
        run: { ...data.RUN_DETAILS["gateway-review"], document_file: DOCUMENT_PATH }, offset: 0, text: "" } });
      api.answer("GET /runs/output/gateway-review/artifacts", { json: [{ key: "document", title: "Document",
        kind: "markdown", items: [{ path: DOCUMENT_PATH, name: "gateway-review.md", size_bytes: DOCUMENT.length }] }] });
      api.answer(`GET /runs/output/gateway-review/files/${DOCUMENT_PATH}`, { body: DOCUMENT,
        headers: { "Content-Type": "text/markdown" } });
      await page.goto("/");
      await page.getByRole("button", { name: /^Done\s*2/ }).click();
      await page.getByText("Gateway review.mp4").click();
      // Open document opens the reader full screen
      await page.getByRole("button", { name: "Open document" }).click();
      const contents = page.getByRole("navigation", { name: "contents" });
      await expect(contents).toBeVisible();
      const firstLink = contents.getByRole("link", { name: HEADINGS[0] });
      const toggle = contents.getByRole("button", { name: "Contents" });
      if (size.width < 900) {
        const box = (await contents.boundingBox())!;
        expect(box.height, "folded, the list is one bar").toBeLessThan(60);
        await expect(firstLink).toBeHidden();
        await toggle.tap();
        await expect(toggle).toHaveAttribute("aria-expanded", "true");
        await expect(firstLink).toBeVisible();
        expect((await contents.boundingBox())!.height, "open, it leaves the text room").toBeLessThanOrEqual(size.height * 0.6 + 1);
        await contents.getByRole("link", { name: HEADINGS[5] }).tap();
        await expect(firstLink, "a heading picked folds the list again").toBeHidden();
      } else {
        await expect(toggle).toBeHidden();
        await expect(firstLink).toBeVisible();
      }
      const box = (await contents.boundingBox())!;
      expect(box.x, "starts on the screen").toBeGreaterThanOrEqual(0);
      expect(box.x + box.width, "ends on the screen").toBeLessThanOrEqual(size.width);
      await page.screenshot({ path: `../temp/reading-contents/${size.width}.png` });
    });
  });
}
