// A phase's worker, live, and the chat on the final document, at every screen width (the owner: "should be in all flows
// types", "a chat option in the final docuemnt in the artifact tab").
import * as data from "./fixtures/data";
import { expect, test } from "./fixtures/mockApi";
import type { Page } from "@playwright/test";
import { expectInsideScreen, SCREEN_WIDTHS } from "./fixtures/layout";

const WORKER = { number: 3, step: "frames-1-4", thinking: "I read the slide's table.", thinking_at: null, can_ask: false,
  speed: { tokens_per_second: 18.2, is_estimated: true, prompt_tokens: 0, is_writing: true },
  doing: { what: "thinking or writing", since: null },
  steps: [{ at: "2026-11-02T07:58:26+01:00", call: 0, tool: "frames-1-4", summary: "attempt 1", args: "", result: "" }], messages: [] };
const CHAT = { thinking: "", thinking_at: null, doing: { what: "the program runs its tools", since: null }, steps: [{ at: null, call: 1, tool: "edit_blocks", summary: "1 edit: replace B3",
  args: "{}", result: "1 edit(s) applied." }], is_worker_running: false,
  messages: [{ at: null, text: "Say tenant shard.", role: "user", is_question: true, is_sent: true, answer: "I changed the routing sentence." }] };
const ARTIFACTS = [
  { key: "document", title: "Document", kind: "markdown", items: [{ path: "gateway/gateway.md", name: "gateway.md", size_bytes: 40 }] },
  { key: "notes", title: "Notes", kind: "markdown", items: [{ path: "03_notes.md", name: "03_notes.md", size_bytes: 20 }] }];

function answerArtifacts(api: { answer: (route: string, reply: { json?: unknown; body?: string }) => void }) {
  api.answer("GET /runs/output/gateway-review/artifacts", { json: ARTIFACTS });
  api.answer("GET /runs/output/gateway-review/files/gateway/gateway.md", { body: "# Gateway\n\nThe routing sentence.\n" });
  api.answer("GET /runs/output/gateway-review/files/03_notes.md", { body: "The notes.\n" });
}

async function openDoneRun(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: /^Done\s*2/ }).click();
  await page.getByText("Gateway review.mp4").click();
  await expect(page.getByRole("heading", { name: "gateway-review", level: 2 })).toBeVisible();
}

// A fast run's phase 3 session as the server lists it, and its turns: each frame's call with its thinking and its note
const FAST_BATCH = { slug: "fast-notes-000-004", frames: "0–4", step: "frame notes", started_at: "2026-11-16T13:42:00",
  duration_s: 60, status: "written", attempts: 1, prompt_tokens: 21727, output_tokens: 695, rejections: [] };
const FAST_SESSION = { step: "", thinking: "", thinking_at: null, can_ask: false, can_send: true,
  doing: { what: "", since: null }, messages: [], steps: [],
  turns: [{ at: 1794835000, call: 0, thinking: "The form lists the gateway's three tenants.", text: "",
    steps: [{ at: 1794835000, call: 0, tool: "submit_note", summary: "frame 0",
      args: JSON.stringify({ description: "- the gateway form with three tenants" }), result: "Noted." }] }] };

for (const size of SCREEN_WIDTHS) {
  test(`a fast run's Frames table names each session's frames and opens its turns at ${size.width} defect`, async ({ page, api }) => {
    // The defect: a fast run's table showed an empty Frames column and the session's code name as its step, and
    // nothing opened a session's thinking. The fix: "0–4 · frame notes", its step a link to its turns, no rejection column.
    await page.setViewportSize(size);
    api.answer("GET /runs/output/gateway-review/batches", { json: [FAST_BATCH] });
    api.answer("GET /runs/output/gateway-review/fast-sessions/fast-notes-000-004", { json: FAST_SESSION });
    await openDoneRun(page);
    await page.getByRole("button", { name: /frames processing/ }).click();
    const tableButton = page.locator("button[aria-expanded]").filter({ hasText: "Frames" }).last();
    if ((await tableButton.getAttribute("aria-expanded")) !== "true") await tableButton.click();
    const table = page.locator(".batch-history table");
    await expect(table.locator("tbody tr").first()).toContainText("0–4");
    await expect(table.locator("thead")).not.toContainText("Rejections");
    await expectInsideScreen(page, page.locator(".batch-history"));
    await table.getByRole("button", { name: /frame notes/ }).click();
    const panel = page.getByRole("dialog", { name: /^Frames 0–4/ });
    await expect(panel.getByRole("tab")).toHaveText([/^Turns \(1\)/]);
    await panel.getByText(/^Thinking · /).click();
    await expect(panel.getByText("The form lists the gateway's three tenants.")).toBeVisible();
    await expect(panel).toContainText("the gateway form with three tenants");
    await expectInsideScreen(page, panel);
    await page.screenshot({ path: `../temp/fast-table/session-panel-${size.width}.png` });
  });

  test(`a phase card's worker and help sit in one row in its corner at ${size.width} defect`, async ({ page }) => {
    // The defect: the eye stood under the "?" in a column of its own (the owner: "in ugly position"). The fix: both
    // in one row in the card's top-right corner.
    await page.setViewportSize(size);
    await openDoneRun(page);
    const card = page.locator("li.step", { has: page.getByRole("button", { name: "open the worker of phase 3" }) });
    const eye = await card.locator(".step-worker").boundingBox();
    const help = await card.getByRole("button", { name: "what phase 3 does" }).boundingBox();
    if (!eye || !help) throw new Error("phase 3's card shows no eye or help");
    expect(Math.abs(eye.y - help.y)).toBeLessThan(4);
    expect(eye.x + eye.width).toBeLessThanOrEqual(help.x + 1);
  });

  test(`a phase's card opens its worker's thinking and steps at ${size.width}`, async ({ page, api }) => {
    await page.setViewportSize(size);
    api.answer("GET /runs/output/gateway-review/phases/3/worker", { json: WORKER });
    await openDoneRun(page);
    await page.getByRole("button", { name: "open the worker of phase 3" }).click();
    const panel = page.getByRole("dialog", { name: /^Phase 3/ });
    await expect(panel.getByText("Step: frames-1-4")).toBeVisible();
    // its steps are turns, the newest holding its thinking folded (the owner: "rename steps to turns", "the thinking tab
    // is not needed"), and a tool call shows its name in words with an icon
    await expect(panel.getByRole("tab")).toHaveText([/^Turns \(1\)/]);
    await expect(panel.getByRole("list", { name: "turns of phase 3" })).toContainText("Frames-1-4");
    await panel.getByText(/^Thinking · 5 words/).click();
    await expect(panel.getByText("I read the slide's table.")).toBeVisible();
    await expect(panel.locator(".tool-call-head svg")).toHaveCount(1);
    // Defect, seen on a real run: each step of a conversation read "Invalid Date", and a finished worker "Writing at 0.2".
    await expect(panel).not.toContainText("Invalid Date");
    await expect(panel).not.toContainText("Writing at");
    await expectInsideScreen(page, panel);
    await page.screenshot({ path: `../temp/worker-live-widths/phase-worker-${size.width}.png` });
  });

  test(`a phase with no worker offers none, and the worker's icon leaves the title readable at ${size.width} defect`, async ({ page, api }) => {
    // A markdown conversion's cards offered a Worker button on phases that call no model, each opening "404: Not Found",
    // and the button took the title's room; the server now says which phase had a worker, and it is an icon in the corner.
    await page.setViewportSize(size);
    const detail = data.RUN_DETAILS["gateway-review"];
    api.answer("GET /runs/output/gateway-review/update", { json: { offset: 0, text: "", run: { ...detail, phases: detail.phases.map((phase) =>
      phase.number === 2 || phase.number === 4 ? { ...phase, is_model_used: false, has_worker: false } : phase) } } });
    await openDoneRun(page);
    await expect(page.getByRole("button", { name: "open the worker of phase 3" })).toBeVisible();
    await expect(page.getByRole("button", { name: "open the worker of phase 2" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "open the worker of phase 4" })).toHaveCount(0);
    for (const number of [3, 5, 6]) {
      const card = page.locator("li.step", { has: page.getByRole("button", { name: `open the worker of phase ${number}` }) });
      const title = await card.locator(".step-name").boundingBox();
      const button = await card.locator(".step-worker").boundingBox();
      if (!title || !button) throw new Error(`phase ${number}'s card shows no title or button`);
      const isApart = title.x + title.width <= button.x || button.x + button.width <= title.x
        || title.y + title.height <= button.y || button.y + button.height <= title.y;
      expect(isApart, `phase ${number}'s title and button are apart`).toBe(true);
      expect(await card.locator(".step-name").evaluate((name) => name.scrollWidth <= name.clientWidth + 1
        || getComputedStyle(name).display === "inline")).toBe(true);
    }
    await page.locator("ol.steps, ol:has(li.step)").first().screenshot({ path: `../temp/worker-live-widths/phase-cards-${size.width}.png` });
  });

  test(`a markdown conversion's card shows no table of steps at ${size.width} defect`, async ({ page, api }) => {
    // The card opened "Writing the table of contents", its first step "running", though the phase calls no model; other
    // workflows keep their table. The page now shows none for a conversion, whatever the server still lists.
    await page.setViewportSize(size);
    const detail = { ...data.RUN_DETAILS["gateway-review"], pdf_mode: "markdown-conversion" as const,
      document_stages: [{ name: "table of contents", counts: { running: 1, waiting: 1 } }] };
    api.answer("GET /runs/output/gateway-review/update", { json: { offset: 0, text: "", run: detail } });
    await openDoneRun(page);
    await page.locator("li.step", { hasText: "document writing" }).locator(".step-choose").click();
    await expect(page.locator("li.step.chosen")).toHaveCount(1);
    await expect(page.getByText("Writing the table of contents")).toHaveCount(0);
    await expect(page.getByText(/stage \d+ of \d+/)).toHaveCount(0);
  });

  test(`a question the server cannot take says so, its words kept, at ${size.width} defect`, async ({ page, api }) => {
    // Ask on a server with no route for it changed nothing but a small grey "405" line; it now says it was not sent and
    // what to do, in a red box, and the words stay in the box.
    await page.setViewportSize(size);
    answerArtifacts(api);
    api.answer("GET /runs/output/gateway-review/document-chat", { json: { ...CHAT, messages: [], steps: [],
      speed: { tokens_per_second: 0, is_estimated: true, prompt_tokens: 0, is_writing: true } } });
    api.answer("POST /runs/output/gateway-review/document-chat/messages", { status: 405, json: { detail: "Method Not Allowed" } });
    await openDoneRun(page);
    await page.getByRole("tab", { name: "Artifacts" }).click();
    await page.getByRole("button", { name: "Chat about this document" }).click();
    const panel = page.getByRole("dialog", { name: "Chat about this document" });
    await panel.getByRole("textbox", { name: "question to the worker" }).fill("Say tenant shard.");
    await panel.getByRole("button", { name: "Ask" }).click();
    const alert = panel.getByRole("alert");
    await expect(alert).toContainText("Not sent");
    await expect(alert).toContainText("Restart the server");
    await expect(panel.getByRole("textbox", { name: "question to the worker" })).toHaveValue("Say tenant shard.");
    await expect(panel).not.toContainText("0 tokens a second");
    await expectInsideScreen(page, alert);
    await page.screenshot({ path: `../temp/worker-live-widths/send-refused-${size.width}.png` });
  });

  test(`the final document's chat asks, answers and shows its steps at ${size.width}`, async ({ page, api }) => {
    await page.setViewportSize(size);
    answerArtifacts(api);
    api.answer("GET /runs/output/gateway-review/document-chat", { json: { ...CHAT, messages: [], steps: [] } });
    api.answer("POST /runs/output/gateway-review/document-chat/messages", { json: CHAT });
    await openDoneRun(page);
    await page.getByRole("tab", { name: "Artifacts" }).click();
    await page.getByRole("button", { name: "Chat about this document" }).click();
    const panel = page.getByRole("dialog", { name: "Chat about this document" });
    await expect(panel.getByText("Ask about this document, or ask for a change.")).toBeVisible();
    await expect(panel.getByRole("tab", { name: "Thinking" })).toHaveCount(0);
    await panel.getByRole("textbox", { name: "question to the worker" }).fill("Say tenant shard.");
    await panel.getByRole("textbox", { name: "question to the worker" }).press("Enter");
    await expect(panel.getByRole("list", { name: "messages to the document chat" })).toContainText("I changed the routing sentence.");
    await expect(panel.getByRole("textbox", { name: "question to the worker" })).toHaveValue("");
    await panel.getByRole("button", { name: /What the worker did/ }).click();
    await expect(panel.getByRole("tab", { name: /^Turns/ })).toBeVisible();
    await panel.getByRole("button", { name: /What the worker did/ }).click();
    // Defect, seen on a real chat: with its worker ended the panel still read LIVE and "the program runs its tools".
    await expect(panel.getByText("Now:")).toHaveCount(0);
    await expect(panel.locator(".live-mark")).toHaveCount(0);
    await expectInsideScreen(page, panel);
    await page.screenshot({ path: `../temp/worker-live-widths/document-chat-${size.width}.png` });
  });
}

test("the chat is offered on the final document and on no other file defect", async ({ page, api }) => {
  // The Artifacts tab offered "Chat about this document" on the notes and the transcript too, though the chat changes the
  // final document alone; the button now stands only on the final document.
  answerArtifacts(api);
  await openDoneRun(page);
  await page.getByRole("tab", { name: "Artifacts" }).click();
  await expect(page.getByRole("button", { name: "Chat about this document" })).toBeVisible();
  await page.getByRole("tab", { name: /^Notes/ }).click();
  await expect(page.getByText("The notes.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Chat about this document" })).toHaveCount(0);
});
