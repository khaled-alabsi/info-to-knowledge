// The inbox's merge: an upload of several files asks nothing, and ticked recordings
// that are one meeting are merged into one recording in parts from the bottom row's merge button.
import { expect, test } from "./fixtures/mockApi";
import { INBOX } from "./fixtures/data";
import { expectInsideScreen, SCREEN_WIDTHS } from "./fixtures/layout";

const PARTS = [
  { ...INBOX[0], name: "Sprint review part 2.mp4", video: "inbox/Sprint review part 2.mp4", path: "inbox/Sprint review part 2.mp4" },
  { ...INBOX[0], name: "Sprint review part 10.mp4", video: "inbox/Sprint review part 10.mp4", path: "inbox/Sprint review part 10.mp4" },
];

test("several files picked at once go into the inbox each as its own, with no question", async ({ page, api }) => {
  api.answer("POST /inbox/upload-bundle", { json: { added: ["part 1.mp4", "part 2.mp4"], refused: [] } });
  await page.goto("/");
  await page.getByTestId("upload-picker").setInputFiles([
    { name: "part 1.mp4", mimeType: "video/mp4", buffer: Buffer.from("one") },
    { name: "part 2.mp4", mimeType: "video/mp4", buffer: Buffer.from("two") }]);
  await expect(page.getByText("saved part 1.mp4, part 2.mp4")).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

for (const size of SCREEN_WIDTHS) {
  test(`two ticked recordings merge into one in name order on a ${size.width} px screen`, async ({ page, api }) => {
    await page.setViewportSize(size);
    api.answer("GET /inbox", { json: [...PARTS, ...INBOX] });
    api.answer("POST /inbox/merge", { json: { video: "inbox/Sprint review", name: "Sprint review" } });
    await page.goto("/");
    const merge = page.getByRole("button", { name: "merge into one recording" });
    await expect(merge).toBeDisabled();
    for (const part of PARTS) await page.getByRole("checkbox", { name: `tick ${part.name}` }).check();
    await expect(merge).toBeEnabled();
    await expectInsideScreen(page, page.getByRole("group", { name: "ticked rows" }));
    await merge.click();
    const dialog = page.getByRole("dialog", { name: "Merge 2 recordings into one" });
    await expectInsideScreen(page, dialog);
    // part 2 plays before part 10, and the name is the one they share
    await expect(dialog.getByRole("listitem")).toHaveText([/Sprint review part 2\.mp4/, /Sprint review part 10\.mp4/]);
    await expect(dialog.getByRole("textbox", { name: "name of the merged recording" })).toHaveValue("Sprint review");
    await dialog.getByRole("button", { name: /^Merge$/ }).click();
    await expect.poll(() => api.sentTo("POST /inbox/merge").length).toBe(1);
    expect(api.sentTo("POST /inbox/merge")[0].body).toEqual({
      videos: ["inbox/Sprint review part 2.mp4", "inbox/Sprint review part 10.mp4"], name: "Sprint review" });
    await expect(dialog).toHaveCount(0);
  });
}
