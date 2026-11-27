// The Instructions page behind the settings gear: a workflow's steps, one file read, edited,
// compared with its default, saved, and every part inside the screen at the four widths.
import { expect, test } from "./fixtures/mockApi";
import type { ApiMock } from "./fixtures/mockApi";
import type { Page } from "@playwright/test";
import { expectInsideScreen, SCREEN_WIDTHS } from "./fixtures/layout";

const CRITIQUE = "instructions/critique.md";
const TERMS = "instructions/_shared/known-terms.md";
// An invented instruction: a heading, a list, a shared guideline and the anchor the program fills
const CRITIQUE_TEXT = "# Your job\n\nName what went wrong in the Gateway notes.\n\n1. A fact said and never written.\n"
  + "2. A label nobody quoted.\n\n{{SHARED:known-terms}}\n\n{{INJECT:base}}\n";
const ADDED_LINE = "Name the AcmeSync retry count as written.";

const CATALOGUE = {
  workflows: [
    { key: "video", title: "Video", variants: [{ key: "quality", title: "quality" }],
      phases: [{ number: 3, title: "frames processing", steps: [
        { key: "critique", title: "Critique", who: "Phase 3 of a video, the follow-up after each batch's notes.",
          variants: ["quality"], files: [CRITIQUE, TERMS] }] }] },
    { key: "audio", title: "Audio", variants: [{ key: "takeaway", title: "takeaway" }], phases: [] },
    { key: "pdf", title: "PDF", variants: [{ key: "pages-as-images", title: "pages as images" }], phases: [] },
  ],
  files: [
    { id: CRITIQUE, title: "Critique", kind: "instruction", used_by: [{ workflow: "video", step: "critique" }],
      is_edited: false, is_default_changed: false },
    { id: TERMS, title: "Shared guideline: known terms", kind: "shared", used_by: [{ workflow: "video", step: "critique" }],
      is_edited: false, is_default_changed: false },
  ],
};
const FILE = { ...CATALOGUE.files[0], default: CRITIQUE_TEXT, text: CRITIQUE_TEXT, default_at_edit: "", versions: 0 };

async function openInstructions(page: Page, api: ApiMock) {
  api.answer("GET /instructions", { json: CATALOGUE });
  api.answer("GET /instructions/file", { json: FILE });
  api.answer("GET /instructions/versions", { json: [] });
  api.answer("POST /instructions/preview", { json: { text: "# Your job\n\nThe whole prompt.", error: "" } });
  api.answer("PUT /instructions/file", { json: { is_saved: true, problems: [], at: "20261106T191000.000000", is_changed: true } });
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByRole("tab", { name: "Instructions" }).click();
  await expect(page.getByRole("heading", { name: "Instructions", level: 2 })).toBeVisible();
}

async function openCritique(page: Page) {
  await page.locator("summary", { hasText: "Critique" }).click();
  await page.getByRole("button", { name: "Critique (instruction), default" }).click();
  await expect(page.getByRole("heading", { name: "Critique", level: 3 })).toBeVisible();
}

test("opens a step's file rendered, edits it, compares it with the default and saves it", async ({ page, api }) => {
  await openInstructions(page, api);
  await expect(page.getByText("Phase 3 · frames processing")).toBeVisible();
  await openCritique(page);
  await expect(page.getByText("Phase 3 of a video, the follow-up after each batch's notes.")).toBeVisible();
  await expect(page.locator(".instruction-markdown li", { hasText: "A label nobody quoted." })).toBeVisible();
  await expect(page.getByRole("button", { name: "Shared guideline: known terms ↗" })).toBeVisible();

  await page.getByRole("tab", { name: "Edit" }).click();
  const source = page.getByRole("textbox", { name: "the text of Critique" });
  await source.fill(`${CRITIQUE_TEXT}${ADDED_LINE}\n`);
  await expect(page.locator(".instruction-live")).toContainText(ADDED_LINE);
  await page.getByRole("tab", { name: "Compare" }).click();
  await page.getByRole("combobox", { name: "Right" }).selectOption("draft");
  await expect(page.locator(".diff-row.change pre.added")).toContainText(ADDED_LINE);

  await page.getByRole("tab", { name: "Edit" }).click();
  await page.getByRole("textbox", { name: "why you changed it" }).fill("retry counts");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  await expect(page.getByText("Critique saved. The next call of a running run reads it.")).toBeVisible();
  expect(api.sentTo("PUT /instructions/file").map((request) => request.body)).toEqual([
    { id: CRITIQUE, text: `${CRITIQUE_TEXT}${ADDED_LINE}\n`, note: "retry counts", kind: "edit" }]);
});

test("the Shown for list's focus ring is drawn whole inside the tree at every screen width defect", async ({ page, api }) => {
  // Defect: the tree scrolls, so it cut the focused list's blue ring at its left and right edges.
  // Fix: the tree's padding on every side leaves the ring room
  await openInstructions(page, api);
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    const select = page.locator(".instruction-variant select");
    await select.focus();
    const room = await select.evaluate((element) => {
      const tree = element.closest(".instructions-tree")!;
      const box = element.getBoundingClientRect();
      const pane = tree.getBoundingClientRect();
      return { left: box.left - (pane.left + tree.clientLeft),
               right: pane.left + tree.clientLeft + tree.clientWidth - box.right };
    });
    expect(room.left, `room left of the list at ${size.width} px`).toBeGreaterThanOrEqual(3);
    expect(room.right, `room right of the list at ${size.width} px`).toBeGreaterThanOrEqual(3);
    await page.screenshot({ path: `../temp/instructions-ring-widths/shown-for-${size.width}.png` });
  }
});

test("the tree, an open file, its editor and its comparison lie inside every screen width", async ({ page, api }) => {
  await openInstructions(page, api);
  await openCritique(page);
  await page.getByRole("tab", { name: "Edit" }).click();
  await page.getByRole("textbox", { name: "the text of Critique" }).fill(`${CRITIQUE_TEXT}${ADDED_LINE}\n`);
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await page.getByRole("tab", { name: "Edit" }).click();
    await expectInsideScreen(page, page.locator(".instructions-file"));
    await expectInsideScreen(page, page.locator(".instruction-actions"));
    await page.getByRole("tab", { name: "Compare" }).click();
    await page.getByRole("combobox", { name: "Right" }).selectOption("draft");
    await expectInsideScreen(page, page.locator(".diff"));
    const scrollsSideways = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    expect(scrollsSideways, `the page scrolls sideways at ${size.width} px`).toBe(false);
  }
  // On a phone the tree comes back with the back button, the file hidden
  await page.setViewportSize(SCREEN_WIDTHS[0]);
  await page.getByRole("button", { name: "Every step" }).click();
  await expectInsideScreen(page, page.locator(".instructions-tree"));
});
