// A run opened in the detail view: its header, its phase cards, the progress bar a card shows, the help behind a
// card's "?", the critique and blocks cards, the log, and the button that unblocks a blocked phase.
import { expect, test } from "./fixtures/mockApi";
import type { Page } from "@playwright/test";
import { buildJob, PHASE_MODELS, PHASES, RUN_DEFAULTS, RUN_DETAILS } from "./fixtures/data";
import { expectInsideScreen, SCREEN_WIDTHS } from "./fixtures/layout";

async function openDoneRun(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: /^Done\s*2/ }).click();
  await page.getByText("Gateway review.mp4").click();
  await expect(page.getByRole("heading", { name: "gateway-review", level: 2 })).toBeVisible();
}

test("a Stop waiting for a running run is said in words, its button takes it back, inside the screen at every width", async ({ page, api }) => {
  const detail = { ...RUN_DETAILS["gateway-review"], live_pids: [4242], signal: "stop" };
  api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
  api.answer("POST /runs/output/gateway-review/control", { json: { signal: null } });
  await openDoneRun(page);
  const line = page.getByRole("status").filter({ hasText: "Stop requested: the run stops at its next check" });
  const cancel = page.getByRole("button", { name: "Cancel the stop" });
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await expectInsideScreen(page, line);
    await expectInsideScreen(page, cancel);
  }
  await cancel.click();
  await expect.poll(() => api.sentTo("POST /runs/output/gateway-review/control").at(-1)?.body).toEqual({ signal: "continue" });
});

test("a knowledge pass interrupted in phase 7 offers Resume, inside the screen at every width, and no stale stop", async ({ page, api }) => {
  // The owner interrupted a phase 7 pass and found no Resume, only "Run again", and a "stop requested" left over
  api.answer("GET /phases", { json: [...PHASES, { number: 7, name: "document_reshaping", title: "document reshaping",
    summary: "Reshapes the document.", reads: "the document", writes: "07_reshaping/report.md", is_model_used: true, is_on: false }] });
  const base = RUN_DETAILS["gateway-review"];
  const detail = { ...base, status: "interrupted", signal: "stop", document_pass: "knowledge",
    phases: [...base.phases, { number: 7, name: "document_reshaping", title: "document reshaping", status: "not run" }] };
  api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
  await openDoneRun(page);
  const resume = page.getByRole("button", { name: /Resume from 7/ });
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await expectInsideScreen(page, resume);
  }
  await expect(page.getByText("stop requested")).toHaveCount(0);
});

test("Resume's arrow resumes with other settings, which win over the run's own, inside the screen at every width", async ({ page, api }) => {
  // The owner moved a run to this Mac's model only by choosing the local settings in a menu; Resume had no such menu
  const base = RUN_DETAILS["gateway-review"];
  const detail = { ...base, status: "interrupted", settings_profile: "strict",
    phases: base.phases.map((phase) => (phase.number >= 4 ? { ...phase, status: "not run" } : phase)) };
  api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
  api.answer("POST /jobs", { json: [buildJob("job-9", { status: "queued" })] });
  await openDoneRun(page);
  const arrow = page.getByRole("button", { name: "resume with another provider set or other settings" });
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await expectInsideScreen(page, page.getByRole("button", { name: /Resume from 4/ }));
    await expectInsideScreen(page, arrow);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await arrow.click();
  await expect(page.getByRole("menuitem", { name: /profile: strict.*the run's own/ })).toBeVisible();
  await expectInsideScreen(page, page.getByRole("menuitem", { name: /default settings/ }));
  await page.screenshot({ path: "test-results/resume-arrow-390.png" });
  await page.getByRole("menuitem", { name: /default settings/ }).click();
  await expect.poll(() => api.sentTo("POST /jobs").at(-1)?.body).toMatchObject({ is_resuming: true, profile: "" });
  await page.screenshot({ path: "test-results/resume-arrow.png" });
});

test("a live run's file groups sit in rows on a phone, the live note under them", async ({ page, api }) => {
  // The owner's phone showed one group button per line, the "updating" note squeezed beside them: "ugly layout"
  const detail = { ...RUN_DETAILS["gateway-review"], live_pids: [4242] };
  api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
  const group = (key: string, title: string) => ({ key, title, kind: "markdown",
    items: [{ path: `${key}/one.md`, name: "one.md", size_bytes: 4 }] });
  api.answer("GET /runs/output/gateway-review/artifacts", { json: [group("document", "Document"),
    group("paragraphs", "Paragraphs"), group("figures", "Figures"), group("text", "Text"), group("notes", "Notes")] });
  api.answer("GET /runs/output/gateway-review/files/document/one.md", { body: "# Gateway\n" });
  await page.setViewportSize({ width: 390, height: 900 });
  await openDoneRun(page);
  await page.getByRole("tab", { name: "Artifacts" }).click();
  const chips = page.locator(".artifact-bar .chip");
  await expect(chips.first()).toBeVisible();
  const tops = await chips.evaluateAll((all) => all.map((chip) => Math.round(chip.getBoundingClientRect().top)));
  expect(new Set(tops).size).toBeLessThan(tops.length);
  const noteTop = await page.locator(".artifact-bar .live-note").evaluate((note) => note.getBoundingClientRect().top);
  expect(noteTop).toBeGreaterThan(Math.max(...tops));
});

test("a large document opens a part at a time on a phone, Show more inside the screen", async ({ page, api }) => {
  // A 6 MB document drawn whole made the owner's phone reload the page again and again
  const paragraph = `${"The gateway routes the tenant to its shard. ".repeat(20)}\n\n`;
  api.answer("GET /runs/output/gateway-review/artifacts", { json: [{ key: "document", title: "Document", kind: "markdown",
    items: [{ path: "gateway/gateway.md", name: "gateway.md", size_bytes: 2_000_000 }] }] });
  api.answer("GET /runs/output/gateway-review/files/gateway/gateway.md", { body: `# Gateway\n\n${paragraph.repeat(2000)}` });
  await page.setViewportSize({ width: 390, height: 844 });
  await openDoneRun(page);
  await page.getByRole("tab", { name: "Artifacts" }).click();
  const more = page.getByRole("button", { name: "Show more" });
  await expect(page.getByText(/^Shown: \d+% of the document/)).toBeVisible();
  await more.scrollIntoViewIfNeeded();
  await expectInsideScreen(page, more);
  await more.click();
  await expect(page.getByText(/^Shown: \d+% of the document \(600k/)).toBeVisible();
});

const PHASE_CARD_TITLES = ["transcribe", "frames preparing", "frames processing", "document writing", "review", "lessons"];

test("shows the run's header and one card per phase", async ({ page }) => {
  await openDoneRun(page);
  const header = page.locator("header", { has: page.getByRole("heading", { name: "gateway-review" }) });

  await expect(header.getByText("Gateway rollout plan")).toBeVisible();
  await expect(header.getByText("done", { exact: true })).toBeVisible();
  await expect(header.getByRole("button", { name: "workflow speed: Quality" })).toBeVisible();
  await expect(header.getByText("Settings: default")).toBeVisible();
  // the run time as two labelled rows: the total, and the time each frame took on average
  await expect(header.getByText("Total processing time")).toBeVisible();
  await expect(header.getByText("12m 00s", { exact: true })).toBeVisible();
  await expect(header.getByRole("button", { name: "remove tag gateway" })).toBeVisible();

  // each card is a button that shows its phase's progress
  const cards = page.getByRole("button").filter({ hasText: /complete\s*·\s*2m 00s/ });
  await expect(cards).toHaveCount(PHASE_CARD_TITLES.length);
  for (const [index, title] of PHASE_CARD_TITLES.entries()) {
    await expect(cards.nth(index)).toContainText(title);
  }
  await expect(page.getByRole("tab", { name: "Progress" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Artifacts" })).toBeVisible();
  // the run keeps no recording, so it has no Recording tab
  await expect(page.getByRole("tab", { name: "Recording" })).toHaveCount(0);
});

test("a video run's header shows no processing mode", async ({ page }) => {
  await openDoneRun(page);
  const header = page.locator("header", { has: page.getByRole("heading", { name: "gateway-review" }) });
  await expect(header.getByText("Settings: default")).toBeVisible();
  await expect(header.getByText(/^Processing:/)).toHaveCount(0);
});

test("a PDF run's header shows its processing mode as a pill", async ({ page, api }) => {
  const detail = { ...RUN_DETAILS["gateway-review"], pdf_mode: "markdown-conversion" as const };
  api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
  await openDoneRun(page);
  const header = page.locator("header", { has: page.getByRole("heading", { name: "gateway-review" }) });
  await expect(header.getByText("Processing: Text")).toBeVisible();
  await expect(header.getByText("Document: Markdown")).toBeVisible();
  // a PDF read as text takes the audio route, yet it has no ingesting profile of people speaking to show
  await expect(header.getByText(/^Ingesting profile/)).toHaveCount(0);
});

for (const size of SCREEN_WIDTHS) {
  test(`a PDF's phase 1 shows its reading step by step, with time left and a stalled last progress, on a ${size.width} px screen defect`, async ({ page, api }) => {
    // Defect: a 6,761-page PDF stood at 6,761 of 6,761 for hours, with no sign of its step or whether it still moved
    // Fix: each step is a row in pages, the bar names the step with its time left, and "Last progress" turns orange
    await page.setViewportSize(size);
    const base = RUN_DETAILS["gateway-review"];
    const waiting = { status: "waiting" as const, done: 0, total: null, frames: null, seconds: 0 };
    const detail = { ...base, pdf_mode: "markdown-conversion" as const,
      phases: base.phases.map((phase, index) => ({ ...phase, status: index === 0 ? "running" : "not run" })),
      transcribing: [
        { name: "text layer", unit: "pages", status: "done" as const, done: 6761, total: 6761, frames: null, seconds: 600 },
        { name: "figures", unit: "pages", status: "done" as const, done: 6761, total: 6761, frames: null, seconds: 1200 },
        { name: "parsing", unit: "pages", status: "done" as const, done: 6761, total: 6761, frames: null, seconds: 33000 },
        { name: "markdown", unit: "pages", status: "running" as const, done: 1200, total: 6761, frames: null,
          seconds: 3600, seconds_left: 16683, seconds_since_progress: 720 },
        { name: "scanned pages", unit: "pages", ...waiting }, { name: "page cleaning", unit: "pages", ...waiting }] };
    api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
    await openDoneRun(page);
    const bar = page.locator(".card.frames");
    await expect(bar).toContainText("writing markdown · stage 4 of 6 · 1,200 of 6,761 pages");
    const last = bar.locator(".last-progress");
    await expect(last).toHaveText(/Last progress 12m/);
    await expect(last).toHaveClass(/stalled/);
    await expectInsideScreen(page, last);
    await bar.getByRole("button", { name: "show stage details" }).click();
    const table = page.getByRole("table", { name: "stages of reading the PDF" });
    await expect(table.getByRole("row")).toHaveCount(7);
    await expect(table.getByRole("row").nth(4)).toContainText("1,200 of 6,761");
    // the table scrolls inside its box on a narrow screen, never past the card (it ran past it at 390 px)
    await expectInsideScreen(page, page.locator(".batch-history .table-scroll"));
  });
}

for (const size of SCREEN_WIDTHS) {
  test(`phase 7 shows its parts with the time left and a table of parts, on a ${size.width} px screen defect`, async ({ page, api }) => {
    // Defect: a knowledge pass's progress said "this phase records no steps to count", though its parts are known
    // Fix: the bar counts the parts and names the running one with the time left; its table lists every part
    await page.setViewportSize(size);
    const base = RUN_DETAILS["gateway-review"];
    const detail = { ...base, pdf_mode: "markdown-conversion" as const, document_pass: "knowledge",
      phases: [...base.phases.map((phase) => ({ ...phase, status: "complete" })),
        { number: 7, name: "document_reshaping", status: "running", title: "LLM knowledge reshaping" }],
      document_pass_parts: [
        { number: 1, status: "done" as const, blocks: 1046, tokens: 52300, sections: 41, calls: 9, seconds: 1800,
          document: "07_reshaping/parts/part-001.md" },
        { number: 2, status: "running" as const, blocks: 662, tokens: 50100, sections: 18, calls: 3, compactions: 2,
          seconds: 600, thinking: "07_reshaping/thinking/part-002.md", thinking_words: 3400, thinking_seconds: 720 },
        { number: 3, status: "waiting" as const, blocks: 1047, tokens: 51800, sections: 37, calls: 0, seconds: 0 },
        { number: 4, status: "waiting" as const, blocks: 485, tokens: 50400, sections: 22, calls: 0, seconds: 0 },
        { number: 5, status: "waiting" as const, blocks: 425, tokens: 21000, sections: 15, calls: 0, seconds: 0 }] };
    api.answer("GET /runs/output/gateway-review/files/07_reshaping/thinking/part-002.md",
      { body: "I compare the router section with the new facts.\n" });
    api.answer("GET /runs/output/gateway-review/artifacts", { json: [
      { key: "cleanup", title: "Clean up", kind: "markdown", items: [
        { path: "07_reshaping/parts/part-001.md", name: "part-001.md", size_bytes: 60 }] }] });
    api.answer("GET /runs/output/gateway-review/files/07_reshaping/parts/part-001.md",
      { body: "# Gateway rollout plan\n\n## Refund limits\n\nThe draft after part 1.\n" });
    api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
    await openDoneRun(page);
    const bar = page.locator(".card.frames");
    await expect(bar).toContainText("Reshaping knowledge");
    await expect(bar).toContainText("part 2 of 5");
    await expect(bar).toContainText("about 1h 50m left");
    await expectInsideScreen(page, bar.locator(".legend"));
    await bar.getByRole("button", { name: "show part details" }).click();
    const table = page.getByRole("table", { name: "parts of the document pass" });
    await expect(table.getByRole("row")).toHaveCount(6);
    await expect(table.getByRole("columnheader")).toHaveText(["Part", "Status", "Size", "Calls", "Time"]);
    await expect(table.getByRole("columnheader", { name: "Blocks" })).toHaveCount(0);
    // each part's compactions under its calls (the owner: "i want to see number of compaction somewhere")
    await expect(table.getByRole("row").nth(2).getByRole("cell").nth(3)).toContainText("2 compactions");
    await expect(table.getByRole("row").nth(1)).toContainText("52,300");
    await expectInsideScreen(page, page.locator(".batch-history .table-scroll"));
    await page.screenshot({ path: `../temp/pass-parts-widths/parts-${size.width}.png`, fullPage: true });
    // the owner: "clicking on the part should open the part document and not the instructions"; a part not done yet
    // has no document, so no link
    await expect(table.getByRole("button", { name: "part 2" })).toHaveCount(0);
    // the owner: "how can i know its not stocked": the running part says how long its model writes, and its thinking
    await expect(table.getByRole("row").nth(2)).toContainText("running · writing for 12m 00s, 3,400 words of thinking");
    await expectInsideScreen(page, page.locator(".batch-history .table-scroll"));
    await table.getByRole("button", { name: "part 1" }).click();
    await expect(page.getByText("The draft after part 1.")).toBeVisible();
    await expect(page.getByText("What the worker of")).toHaveCount(0);
  });
}

for (const size of SCREEN_WIDTHS) {
  test(`a part waiting for a broken model host says so and not that it writes, on a ${size.width} px screen defect`, async ({ page, api }) => {
    // Defect: LM Studio listed its model READY and answered "Compute error" to every call; the part's row read
    // "writing for 22m 59s, 0 words of thinking". Fix: the row says the part waits for the model host, with its error
    await page.setViewportSize(size);
    const base = RUN_DETAILS["gateway-review"];
    const detail = { ...base, pdf_mode: "markdown-conversion" as const, document_pass: "knowledge",
      phases: [...base.phases.map((phase) => ({ ...phase, status: "complete" })),
        { number: 7, name: "document_reshaping", status: "running", title: "LLM knowledge reshaping" }],
      document_pass_parts: [
        { number: 1, status: "running" as const, blocks: 111, tokens: 31974, sections: 111, calls: 0, seconds: 1379,
          host_wait: "the model answers every call with an error: Compute error.", host_wait_seconds: 1379 },
        { number: 2, status: "waiting" as const, blocks: 27, tokens: 30177, sections: 27, calls: 0, seconds: 0 }] };
    api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
    await openDoneRun(page);
    const bar = page.locator(".card.frames");
    await expect(bar).toContainText("part 1 waits for the model host for 22m 59s: the model answers every call");
    await expect(bar).not.toContainText("part 1 running for");
    await bar.getByRole("button", { name: "show part details" }).click();
    const table = page.getByRole("table", { name: "parts of the document pass" });
    const row = table.getByRole("row").nth(1);
    await expect(row).toContainText("running · waiting for the model host for 22m 59s: "
      + "the model answers every call with an error: Compute error.");
    await expect(row).not.toContainText("writing for");
    await expectInsideScreen(page, page.locator(".batch-history .table-scroll"));
    await page.screenshot({ path: `../temp/host-wait-widths/parts-${size.width}.png`, fullPage: true });
  });
}

for (const size of SCREEN_WIDTHS) {
  test(`the Artifacts tab's Worker turns opens the running worker's turns and lists every session, on a ${size.width} px screen`, async ({ page, api }) => {
    // The owner: "renamed to worker turns", "the drop down should select the session for each worker that already run or
    // curently running, the defulty the currently running", "view turns and tool call just like the layover"
    await page.setViewportSize(size);
    const base = RUN_DETAILS["gateway-review"];
    const detail = { ...base, pdf_mode: "markdown-conversion" as const, document_pass: "knowledge",
      phases: [...base.phases.map((phase) => ({ ...phase, status: "complete", has_worker: phase.number === 4 })),
        { number: 7, name: "document_reshaping", status: "running", title: "LLM knowledge reshaping" }],
      document_pass_parts: [
        { number: 11, status: "done" as const, blocks: 9, tokens: 50100, sections: 3, calls: 4, seconds: 600 },
        { number: 12, status: "running" as const, blocks: 9, tokens: 50100, sections: 3, calls: 2, seconds: 60 },
        { number: 13, status: "waiting" as const, blocks: 9, tokens: 50100, sections: 3, calls: 0, seconds: 0 }] };
    api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
    api.answer("GET /runs/output/gateway-review/artifacts", { json: [
      { key: "document", title: "Document", kind: "markdown", items: [{ path: "gateway/gateway.md", name: "gateway.md", size_bytes: 40 }] },
      { key: "thinking", title: "Worker turns", kind: "markdown", items: [
        { path: "07_reshaping/thinking/part-012.md", name: "part-012.md", size_bytes: 30 }] }] });
    api.answer("GET /runs/output/gateway-review/files/gateway/gateway.md", { body: "# Gateway rollout plan\n" });
    const turn = (thinking: string, tool: string, result: string) => ({ at: null, call: 1, thinking, text: "",
      steps: [{ at: null, call: 1, tool, args: "{\"section\": \"K2\"}", result }] });
    api.answer("GET /runs/output/gateway-review/document-pass/parts/12", { json: { number: 12, thinking_at: null,
      thinking: "I merge the router facts into K2.", doing: { what: "thinking", since: null },
      steps: [], turns: [turn("I read K2 first.", "read_section", "K2 ## Router")] } });
    api.answer("GET /runs/output/gateway-review/document-pass/parts/11", { json: { number: 11, thinking_at: null,
      thinking: "", steps: [], turns: [turn("Part 11 reads the outline.", "read_knowledge_outline", "K1 Gateway"),
        turn("Part 11 closes.", "finish_batch", "part 11 closed")] } });
    api.answer("GET /runs/output/gateway-review/phases/4/worker", { json: { thinking: "", steps: [], turns: [
      turn("Phase 4 plans its chapters.", "read_notes", "chapter 1 read whole")] } });
    await openDoneRun(page);
    await page.getByRole("tab", { name: "Artifacts" }).click();
    const chips = page.locator(".chips");
    await expect(chips.getByRole("tab", { name: /Worker turns\s*3/ })).toBeVisible();
    await expect(chips.getByRole("tab", { name: /Worker thinking/ })).toHaveCount(0);
    await chips.getByRole("tab", { name: /Worker turns/ }).click();
    const sessions = page.getByRole("combobox", { name: "worker session" });
    // every worker that ran or runs, a waiting part left out, the running one chosen first
    await expect(sessions.locator("option")).toHaveText([/Phase 4/, "Phase 7 · part 11", "Phase 7 · part 12 · running"]);
    await expect(sessions).toHaveValue("part-12");
    const turns = page.getByRole("list", { name: "turns of Phase 7 · part 12" });
    await expect(page.getByText("Now: thinking")).toBeVisible();
    await expect(turns.getByText("Read section")).toBeVisible();
    await expect(turns.getByText("I merge the router facts into K2.")).toBeVisible();
    await expectInsideScreen(page, sessions);
    await expectInsideScreen(page, turns);
    await page.screenshot({ path: `../temp/worker-turns-widths/running-${size.width}.png`, fullPage: true });
    // a finished part's session, its turns and tool calls as the live panel shows them
    await sessions.selectOption("part-11");
    const done = page.getByRole("list", { name: "turns of Phase 7 · part 11" });
    await expect(done.getByText("Read knowledge outline")).toBeVisible();
    await expect(done.getByText("Finish batch")).toBeVisible();
    await expect(page.getByText("Now: thinking")).toHaveCount(0);
    await page.getByRole("button", { name: "previous session" }).click();
    await expect(page.getByRole("list", { name: /turns of Phase 4/ }).getByText("Read notes")).toBeVisible();
  });
}

for (const size of SCREEN_WIDTHS) {
  test(`a running job's hold-the-queue choice is an item of the Control menu, on a ${size.width} px screen`, async ({ page, api }) => {
    // The owner, of the switch beside the Control menu: "still look ugly", "too much text", "merge it to the menu control"
    await page.setViewportSize(size);
    const detail = { ...RUN_DETAILS["gateway-review"], live_pids: [4242] };
    api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
    const running = buildJob("job-running", { status: "running", pid: 4242 },
      { video: "inbox/Gateway review.mp4", output_root: "output/gateway-review", on_failure: "block" });
    api.answer("GET /jobs", { json: [running] });
    api.answer("POST /jobs/job-running/on-failure", { json: running });
    await openDoneRun(page);

    await expect(page.getByText("Hold the queue if this job fails")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "On failure" })).toHaveCount(0);
    await page.getByRole("button", { name: "Control" }).click();
    const choice = page.getByRole("menuitemcheckbox", { name: /Hold queue on failure/ });
    await expect(choice).toHaveAttribute("aria-checked", "true");
    await expect(choice).toContainText("the queue waits for you");
    await expectInsideScreen(page, choice);
    await page.screenshot({ path: `../temp/job-failure-switch/control-${size.width}.png` });
    await choice.click();
    await expect.poll(() => api.sentTo("POST /jobs/job-running/on-failure").map((sent) => sent.body))
      .toEqual([{ choice: "continue" }]);
  });

  test(`a running part's status opens its live steps and thinking, its name on one line, on a ${size.width} px screen defect`, async ({ page, api }) => {
    // Defect: the owner saw a word count only, and "part" over "2" on a phone. Fix: the status opens the part's live
    // panel, its steps and its thinking; a part's name never breaks
    await page.setViewportSize(size);
    const base = RUN_DETAILS["gateway-review"];
    const detail = { ...base, pdf_mode: "markdown-conversion" as const, document_pass: "knowledge",
      phases: [...base.phases.map((phase) => ({ ...phase, status: "complete" })),
        { number: 7, name: "document_reshaping", status: "running", title: "LLM knowledge reshaping" }],
      document_pass_parts: [{ number: 12, status: "running" as const, blocks: 9, tokens: 50100, sections: 3, calls: 2,
        seconds: 60, thinking: "07_reshaping/thinking/part-012.md", thinking_words: 12, thinking_seconds: 45,
        document: "07_reshaping/parts/part-012.md" },
        { number: 13, status: "waiting" as const, blocks: 9, tokens: 50100, sections: 3, calls: 0, seconds: 0 }] };
    api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
    api.answer("GET /runs/output/gateway-review/document-pass/parts/12", { json: { number: 12, thinking_at: null,
      thinking: "I merge the router facts into K2.",
      speed: { tokens_per_second: 7.8, is_estimated: true, prompt_tokens: 0, is_writing: true },
      doing: { what: "thinking", since: null },
      steps: [{ at: null, call: 1, tool: "read_section", args: "{\"section\": \"K2\"}", result: "K2 ## Router" }],
      turns: [{ at: null, call: 1, thinking: "I read K2 first.", text: "",
        steps: [{ at: null, call: 1, tool: "read_section", args: "{\"section\": \"K2\"}", result: "K2 ## Router" }] }] } });
    api.answer("GET /runs/output/gateway-review/files/07_reshaping/parts/part-012.md",
      { body: "# Gateway rollout plan\n\n## Router\n\nThe router picks the shard so far.\n" });
    await openDoneRun(page);
    await page.locator(".card.frames").getByRole("button", { name: "show part details" }).click();
    const table = page.getByRole("table", { name: "parts of the document pass" });
    const name = table.locator("td.part-name span").first();
    const box = await name.boundingBox();
    expect(box!.height).toBeLessThan(30);
    await expect(table.getByRole("button", { name: /^waiting/ })).toHaveCount(0);
    await table.getByRole("button", { name: /^running · writing for/ }).click();
    // an overlay over the page (the owner: "the live pannel should be overlay")
    const panel = page.getByRole("dialog", { name: /^Part 12/ });
    // tabs, the most active first (the owner: "tabs desigen instead of stack desigen"), and the writing speed
    // each turn holds its own thinking, and the chat has a tab of its own (the owner: "rename steps to turns", "the
    // thinking tab is not needed", "make the chat has own tab"); the call under way is the second turn
    await expect(panel.getByRole("tab")).toHaveText([/^Turns \(2\)/, /^Document so far/, /^Chat/]);
    await expect(panel.getByText("Writing at 7.8 tokens a second (estimated from the text written so far)")).toBeVisible();
    // the owner: "i want to know, if the agent now thinking or doing something else"
    await expect(panel.getByText("Now: thinking")).toBeVisible();
    await expect(panel.getByText("I merge the router facts into K2.")).toBeVisible();
    // an earlier turn keeps its own thinking, folded under it
    await panel.getByText("Thinking · 4 words").click();
    await expect(panel.getByText("I read K2 first.")).toBeVisible();
    // the owner: "show live icon where it live is", then "one live icon is enogh in the correct place": the running
    // row's, and one in the overlay's title
    await expect(table.locator(".live-mark")).toHaveCount(1);
    await expect(panel.locator(".live-mark")).toHaveCount(1);
    await expect(panel.getByRole("heading").locator(".live-mark")).toHaveCount(1);
    await expectInsideScreen(page, panel);
    await page.screenshot({ path: `../temp/part-live-widths/part-live-${size.width}.png` });
    // the owner: "chat with worker while it works", "send a message while it worke as selected role"
    api.answer("POST /runs/output/gateway-review/document-pass/parts/12/messages", { json: { number: 12, thinking_at: null,
      thinking: "", steps: [], messages: [{ at: null, text: "How far are you?", role: "user", is_question: true,
        is_sent: true, answer: "Half of the part is written." }] } });
    await panel.getByRole("tab", { name: /^Chat/ }).click();
    await expect(panel.getByRole("button", { name: "Ask a question" })).toHaveAttribute("aria-pressed", "true");
    await panel.getByRole("textbox", { name: "question to the worker" }).fill("How far are you?");
    await panel.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(panel.getByRole("list", { name: "messages to part 12" })).toContainText("Half of the part is written.");
    await panel.getByRole("button", { name: "Send a message" }).click();
    await panel.getByRole("combobox", { name: "Send as" }).selectOption("system");
    await expect(panel.getByText(/reads its whole prompt again/)).toBeVisible();
    // a message waits for no reply: a note across the chat, the questions left to Ask a question (the owner: "i choosed
    // send a message and not open a chat, now it look as i expect an answere")
    api.answer("POST /runs/output/gateway-review/document-pass/parts/12/messages", { json: { number: 12, thinking_at: null,
      thinking: "", steps: [], messages: [{ at: null, text: "How far are you?", role: "user", is_question: true,
        is_sent: true, answer: "Half of the part is written." }, { at: null, text: "Keep the tables.", role: "system",
        is_question: false, is_sent: false, answer: "" }] } });
    await panel.getByRole("textbox", { name: "message to the worker" }).fill("Keep the tables.");
    await panel.getByRole("button", { name: "Send", exact: true }).click();
    const notes = panel.getByRole("list", { name: "messages to part 12" });
    await expect(notes).toContainText("Message as system · waiting for its next call");
    await expect(notes).not.toContainText("How far are you?");
    await expect(notes.locator(".chat-bubble")).toHaveCount(0);
    await expectInsideScreen(page, panel);
    await page.screenshot({ path: `../temp/part-live-widths/part-messages-${size.width}.png` });
    await panel.getByRole("tab", { name: /^Turns/ }).click();
    // a tool call by its name in words, its inputs as name and value, never raw JSON (the owner: "tool call ... should be
    // handled from ui as tool call (text + icon)")
    await expect(panel.getByRole("list", { name: "turns of part 12" })).toContainText("Read section");
    await expect(panel.locator(".tool-call-args").first()).toContainText("section");
    await expect(panel.getByRole("list", { name: "turns of part 12" })).not.toContainText("{\"section\"");
    // the owner: "see the part file as it updated in live"
    await panel.getByRole("tab", { name: /^Document so far/ }).click();
    await expect(panel.getByText("The router picks the shard so far.")).toBeVisible();
    await expectInsideScreen(page, panel);
    // one height whichever tab is open (the owner: "Each tab has its own vertical size, which is bad")
    const heights: number[] = [];
    for (const tab of [/^Turns/, /^Document so far/, /^Chat/]) {
      await panel.getByRole("tab", { name: tab }).click();
      heights.push(Math.round((await panel.boundingBox())!.height));
    }
    expect(new Set(heights).size, `the panel's heights on its three tabs: ${heights.join(", ")}`).toBe(1);
    await panel.getByRole("button", { name: "close" }).click();
    await expect(panel).toHaveCount(0);
  });
}

for (const size of SCREEN_WIDTHS) {
  test(`a PDF run's Document pill names the LLM pass its document was given, on a ${size.width} px screen defect`, async ({ page, api }) => {
    // Defect: a markdown conversion given a knowledge pass showed "Document: Markdown", which said nothing of the pass
    // Fix: the pill names the shaping as the run form lists it, "Markdown + LLM knowledge"
    await page.setViewportSize(size);
    const detail = { ...RUN_DETAILS["gateway-review"], pdf_mode: "markdown-conversion" as const, document_pass: "knowledge" };
    api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
    await openDoneRun(page);
    const header = page.locator("header", { has: page.getByRole("heading", { name: "gateway-review" }) });
    const pill = header.getByText("Document: Markdown + LLM knowledge reshaping");
    await expect(pill).toBeVisible();
    await expect(header.getByText("Document: Markdown", { exact: true })).toHaveCount(0);
    await expectInsideScreen(page, pill);
  });
}

test("a click on phase 3's card shows its frames bar, and a second click hides it", async ({ page }) => {
  await openDoneRun(page);
  const frames = page.getByRole("button", { name: "show batch details" });
  await expect(frames).toHaveCount(0);

  const phaseThree = page.getByRole("button", { name: /frames processing/ }).first();
  await phaseThree.click();
  await expect(phaseThree).toHaveAttribute("aria-pressed", "true");
  await expect(frames).toBeVisible();
  await expect(page.getByText("written 38")).toBeVisible();
  await expect(page.getByText("skipped 2")).toBeVisible();
  await expect(page.getByText("One frame takes 42s on average")).toBeVisible();

  await phaseThree.click();
  await expect(frames).toHaveCount(0);
});

for (const size of SCREEN_WIDTHS) {
  test(`phase 3's steps table fits the screen, a long result wrapped, on a ${size.width} px screen defect`, async ({ page, api }) => {
    // Defect: on a phone the steps table ran past the screen, a refused step's result cut at both sides over an empty box
    // Fix: on a phone each step is a card with every value under its column's name, a long result wrapped
    await page.setViewportSize(size);
    const result = "frames 17, 18 accepted; frames 19, 20, 21, 22 refused";
    api.answer("GET /runs/output/gateway-review/batches", { json: [
      { slug: "frames-019-022-reading", frames: "19–22", step: "reading", started_at: "2026-11-28T17:40:00", duration_s: 250,
        status: result, attempts: 3, prompt_tokens: 35630, output_tokens: 1160,
        rejections: [{ attempt: 1, findings: [{ code: "FRAME_FACTS_MISSING", detail: "frame 21 has no facts" }] }] }] });
    await openDoneRun(page);
    await page.getByRole("button", { name: /frames processing/ }).first().click();
    await page.getByRole("button", { name: "show batch details" }).click();
    const cell = page.locator(".batch-history td", { hasText: result });
    await expect(cell).toBeVisible();
    await expectInsideScreen(page, cell);
    await expectInsideScreen(page, page.locator(".batch-history .table-scroll"));
  });
}

test("the ? on a phase card says what the phase reads and writes", async ({ page }) => {
  await openDoneRun(page);

  await page.getByRole("button", { name: "what phase 4 does" }).click();
  await expect(page.getByText("Phase 4 · document writing")).toBeVisible();
  await expect(page.getByText("Writes the document.")).toBeVisible();
  await expect(page.getByText("04_document.md")).toBeVisible();
  // a click outside closes it (Escape does not while the "?" has focus: its card's wrapper stops every key)
  await page.getByRole("heading", { name: "gateway-review" }).click();
  await expect(page.getByText("Phase 4 · document writing")).toHaveCount(0);
});

test("shows the critique card and the run's log", async ({ page }) => {
  await openDoneRun(page);

  await expect(page.getByText("3 anti-patterns from 9 rules over 4 batches")).toBeVisible();
  await page.getByText("Critique", { exact: true }).click();
  await expect(page.getByText("Quote the speaker's own numbers with their unit.")).toBeVisible();
  await expect(page.getByText("phase 6 lessons complete")).toBeVisible();
});

test("a blocked run offers to unblock its phase and queues the resume", async ({ page, api }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "phase 3 ›" }).click();
  await expect(page.getByRole("heading", { name: "jonas-onboarding", level: 2 })).toBeVisible();

  await expect(page.getByText("Ingesting profile: knowledge")).toBeVisible();
  await expect(page.getByText("1 over 1 step")).toBeVisible();
  // phases 5 and 6 are off for an audio recording
  await expect(page.getByRole("button", { name: /review\s*skipped/ })).toBeVisible();

  // Unblock only opens the choice; the run is queued by "Run it now"
  await page.getByRole("button", { name: "Unblock phase 3 · frames processing…" }).click();
  expect(api.sentTo("POST /jobs")).toHaveLength(0);
  await page.getByRole("button", { name: "Run it now" }).click();
  await expect(page.getByText("Queued an unblock of jonas-onboarding (job job-new-1)")).toBeVisible();
  expect(api.sentTo("POST /jobs")[0].body).toMatchObject({
    items: [{ video: "inbox/Jonas onboarding.m4a", output: "output/jonas-onboarding" }], is_resuming: true,
  });
});

test("the run menu keeps every model choice inside it, each phase's name over its models defect", async ({ page, api }) => {
  // The Run menu put each phase beside its models; a model's long name ran past the menu's right edge and under its
  // Queue button. Inside the menu the phase's name now stands over its models, which wrap within the menu's width.
  api.answer("GET /phase-models", { json: { ...PHASE_MODELS,
    models: [{ name: "acme-vision-35b-a3b-long-name", spec: "lmstudio:acme-vision-35b-a3b-long-name" },
      ...PHASE_MODELS.models] } });
  await openDoneRun(page);
  await page.getByRole("button", { name: "more actions" }).first().click();
  await page.getByRole("menuitem", { name: /Run again…/ }).click();
  const menu = page.getByRole("dialog", { name: "Run again" }).locator(".run-choices");
  await menu.getByRole("tab", { name: /Models/ }).click();

  const menuBox = await menu.boundingBox();
  const choices = menu.locator(".phase-models .btn");
  await expect(choices.first()).toBeVisible();
  for (const choice of await choices.all()) {
    const choiceBox = await choice.boundingBox();
    expect(choiceBox!.x + choiceBox!.width).toBeLessThanOrEqual(menuBox!.x + menuBox!.width);
  }
});

for (const size of SCREEN_WIDTHS) {
  test(`the run's Run dialog offers the Workflow speed in its Settings tab and queues the speed chosen, at ${size.width} px defect`,
    async ({ page, api }) => {
      // Defect: the ··· menu's Run dialog had no Workflow speed, stood taller than the screen with its top out of
      // reach, and queued without the run's speed. Fix: tabs, as the inbox's Run settings, the speed a dropdown in the
      // Settings tab, and the run queued takes it
      await page.setViewportSize(size);
      await openDoneRun(page);
      await page.getByRole("button", { name: "more actions" }).first().click();
      await page.getByRole("menuitem", { name: /Run again…/ }).click();
      const dialog = page.getByRole("dialog", { name: "Run again" });
      await expectInsideScreen(page, dialog);
      await dialog.getByRole("tab", { name: /Settings/ }).click();
      await dialog.getByRole("button", { name: /^workflow speed/ }).click();
      const menu = page.locator(".speed-panel");
      await expectInsideScreen(page, menu.getByRole("radio", { name: /^Balanced/ }));
      await menu.getByRole("radio", { name: /^Balanced/ }).click();
      await page.keyboard.press("Escape");
      await expect(dialog.getByRole("button", { name: "workflow speed: Balanced" })).toBeVisible();
      await dialog.getByRole("button", { name: "Queue this run" }).click();
      await expect.poll(() => api.sentTo("POST /jobs").length).toBe(1);
      expect(api.sentTo("POST /jobs")[0].body).toMatchObject({ speed_preset: "balanced" });
    });
}

test("a done PDF run queues a rewrite by a model as phase 7, with the extra wishes typed", async ({ page, api }) => {
  const detail = { ...RUN_DETAILS["gateway-review"], pdf_mode: "markdown-conversion" as const };
  api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
  await openDoneRun(page);

  await page.getByRole("button", { name: /Change this document/ }).click();
  // the pass first: no box of wishes until one is chosen
  const wishes = page.getByRole("textbox", { name: "extra wishes for the model" });
  await expect(wishes).toHaveCount(0);
  // each pass loads the wishes it was last given, its own
  await page.getByRole("radio", { name: "clean up with LLM" }).click();
  await expect(wishes).toHaveValue(RUN_DEFAULTS.document_wishes.cleanup);
  await page.getByRole("radio", { name: "rewrite with LLM" }).click();
  await expect(wishes).toHaveValue(RUN_DEFAULTS.document_wishes.rewrite);
  await wishes.fill("remove every Jira link");
  await page.getByRole("button", { name: "Queue the rewrite" }).click();

  await expect(page.getByText(/Queued a rewrite of gateway-review/)).toBeVisible();
  expect(api.sentTo("POST /jobs")[0].body).toMatchObject({
    items: [{ output: "output/gateway-review" }], only_phase: 7, is_resuming: true,
    document_pass: "rewrite", document_wishes: "remove every Jira link",
  });
});

for (const size of SCREEN_WIDTHS) {
  test(`the clean up or rewrite panel lies inside a ${size.width} px screen with its box open defect`, async ({ page, api }) => {
    // Defect: at a middle width the panel's options, its box and its button ran past the screen's right edge
    // Fix: the panel takes the wide panel's size and its content never grows past it
    await page.setViewportSize(size);
    const detail = { ...RUN_DETAILS["gateway-review"], pdf_mode: "markdown-conversion" as const };
    api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
    await openDoneRun(page);
    await page.getByRole("button", { name: /Change this document/ }).click();
    await page.getByRole("radio", { name: "clean up with LLM" }).click();
    const panel = page.locator(".dropdown-panel").filter({ has: page.getByRole("radiogroup", { name: "how to change the document" }) });
    await expectInsideScreen(page, panel);
    await expectInsideScreen(page, page.getByRole("button", { name: "Queue the clean up" }));
  });
}

test("a done PDF run queues a knowledge pass with its own wishes", async ({ page, api }) => {
  const detail = { ...RUN_DETAILS["gateway-review"], pdf_mode: "markdown-conversion" as const };
  api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
  await openDoneRun(page);
  await page.getByRole("button", { name: /Change this document/ }).click();
  await expect(page.getByRole("radio")).toHaveText([/clean up with LLM\s*removes noise, keeps every sentence/,
    /rewrite with LLM\s*better structure and writing/,
    /knowledge reshaping with LLM\s*reshapes the document into a knowledge-focused document, in parts/]);
  await page.getByRole("radio", { name: /knowledge reshaping with LLM/ }).click();
  const wishes = page.getByRole("textbox", { name: "extra wishes for the model" });
  await expect(wishes).toHaveValue(RUN_DEFAULTS.document_wishes.knowledge);
  await page.getByRole("button", { name: "Queue the knowledge reshaping" }).click();
  await expect(page.getByText(/Queued a knowledge reshaping of gateway-review/)).toBeVisible();
  expect(api.sentTo("POST /jobs")[0].body).toMatchObject({ only_phase: 7, is_resuming: true,
    document_pass: "knowledge", document_wishes: RUN_DEFAULTS.document_wishes.knowledge });
});

for (const size of SCREEN_WIDTHS) {
  test(`a pass on a changed document asks what it starts from, on a ${size.width} px screen`, async ({ page, api }) => {
    // The owner: a second pass read the last pass's document with no say; it now asks, the original by default
    await page.setViewportSize(size);
    const detail = { ...RUN_DETAILS["gateway-review"], pdf_mode: "markdown-conversion" as const, document_pass: "knowledge" };
    api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
    await openDoneRun(page);
    await page.getByRole("button", { name: /Change this document/ }).click();
    await page.getByRole("radio", { name: /clean up with LLM/ }).click();
    const startFrom = page.getByRole("radiogroup", { name: "start from" });
    await expect(startFrom.getByRole("radio", { name: /the original/ })).toBeChecked();
    await expectInsideScreen(page, startFrom);
    await page.screenshot({ path: `../temp/pass-source-widths/start-from-${size.width}.png` });
    await startFrom.getByRole("radio", { name: /the current document/ }).check();
    await page.getByRole("button", { name: "Queue the clean up" }).click();
    await expect.poll(() => api.sentTo("POST /jobs").length).toBe(1);
    expect(api.sentTo("POST /jobs")[0].body).toMatchObject({ only_phase: 7, document_pass: "cleanup",
      document_source: "current" });
  });
}

test("Edit instruction opens the chosen pass's instruction in the Instructions editor", async ({ page, api }) => {
  const detail = { ...RUN_DETAILS["gateway-review"], pdf_mode: "markdown-conversion" as const };
  api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
  const file = "instructions/_shared/document-knowledge.md";
  api.answer("GET /instructions", { json: { workflows: [{ key: "pdf", title: "PDF", variants: [], phases: [
    { number: 7, title: "clean up, rewrite or knowledge", steps: [{ key: "document_knowledge", title: "Knowledge",
      who: "Phase 7.", variants: [], files: ["instructions/pdf/document_knowledge.md", file] }] }] }],
  files: [{ id: file, title: "Shared guideline: document knowledge", kind: "shared",
    used_by: [{ workflow: "pdf", step: "document_knowledge" }], is_edited: false, is_default_changed: false }] } });
  const asked = page.waitForRequest((request) => request.url().includes("/instructions/file")
    && decodeURIComponent(request.url()).includes(file));
  api.answer("GET /instructions/file", { json: { id: file, title: "Shared guideline: document knowledge",
    kind: "shared", used_by: [], is_edited: false, is_default_changed: false, default: "# Goal\n\nThe goal.\n",
    text: "# Goal\n\nThe goal.\n", default_at_edit: "", versions: 0 } });
  api.answer("GET /instructions/versions", { json: [] });
  await openDoneRun(page);
  await page.getByRole("button", { name: /Change this document/ }).click();
  await page.getByRole("radio", { name: /knowledge reshaping with LLM/ }).click();
  await page.getByRole("button", { name: "Edit instruction" }).click();
  await expect(page.getByRole("tab", { name: "Instructions" })).toHaveAttribute("aria-selected", "true");
  await asked;
});

for (const size of SCREEN_WIDTHS) {
  test(`each pass's description stands under its name, as every menu's items do, on a ${size.width} px screen`, async ({ page, api }) => {
    await page.setViewportSize(size);
    const detail = { ...RUN_DETAILS["gateway-review"], pdf_mode: "markdown-conversion" as const };
    api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
    await openDoneRun(page);
    await page.getByRole("button", { name: /Change this document/ }).click();
    await page.getByRole("radio", { name: /knowledge reshaping with LLM/ }).click();
    const row = page.getByRole("radio", { name: /knowledge reshaping with LLM/ });
    const name = await row.locator(".pass-name").boundingBox();
    const description = await row.locator(".pass-description").boundingBox();
    expect(description!.y).toBeGreaterThanOrEqual(name!.y + name!.height - 1);
    expect(Math.abs(description!.x - name!.x)).toBeLessThan(2);
    await expect(row.locator(".icon")).toBeVisible();
    await expectInsideScreen(page, row);
    await expectInsideScreen(page, page.getByRole("button", { name: "Queue the knowledge reshaping" }));
    await expectInsideScreen(page, page.getByRole("button", { name: "Edit instruction" }));
  });
}

for (const size of SCREEN_WIDTHS) {
  test(`a PDF run's menu names the PDF, never a recording, on a ${size.width} px screen defect`, async ({ page, api }) => {
    // Defect: a PDF run's "PDF" menu offered "Move recording to inbox" and "Move recording to trash"
    // Fix: every line of the menu names the PDF
    await page.setViewportSize(size);
    const detail = { ...RUN_DETAILS["gateway-review"], pdf_mode: "text-only" as const,
      is_recording_kept: true, has_recording: true };
    api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
    await openDoneRun(page);
    await page.getByRole("button", { name: "more actions" }).first().click();
    await expect(page.getByRole("menuitem", { name: /Move PDF to inbox/ })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: /Move PDF to trash/ })).toBeVisible();
    await expect(page.getByRole("menuitem", { name: /recording/ })).toHaveCount(0);
    await expectInsideScreen(page, page.getByRole("menuitem", { name: /Move PDF to trash/ }));
  });
}

for (const size of SCREEN_WIDTHS) {
  test(`a video's transcript stands under the video when both do not fit side by side, on a ${size.width} px screen defect`, async ({ page, api }) => {
    // Defect: beside the lists on a laptop screen the transcript panel stayed beside the video and squeezed it to a
    // stamp, since the layout stacked only by the screen's width. Fix: it stacks by the detail view's own width
    await page.setViewportSize(size);
    const detail = { ...RUN_DETAILS["gateway-review"], has_recording: true,
      recording_parts: [{ name: "Gateway review.mp4", size_bytes: 156_000_000, start_s: 0, duration_s: 600 }] };
    api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
    api.answer("GET /runs/output/gateway-review/transcript", { json: { lines: [
      { start_s: 2, end_s: 6, text: "The router picks the ledger shard from the tenant id.", speaker: "A" }] } });
    api.answer("GET /runs/output/gateway-review/recording", { body: "" });
    await openDoneRun(page);
    await page.getByRole("tab", { name: "Recording" }).click();
    const player = page.locator(".recording-player");
    const side = page.locator(".recording-side");
    await expect(side).toBeVisible();
    const playerBox = await player.boundingBox();
    const sideBox = await side.boundingBox();
    expect(playerBox && sideBox, "both are shown").toBeTruthy();
    if (!playerBox || !sideBox) return;
    const isStacked = sideBox.y >= playerBox.y + playerBox.height - 1;
    expect(isStacked || playerBox.width >= 400,
      `side by side only while the video keeps 400 px: video ${playerBox.width} px wide`).toBe(true);
    await expectInsideScreen(page, side);
    await page.screenshot({ path: `../temp/recording-widths/recording-${size.width}.png`, fullPage: true });
  });

  test(`a PDF's tab shows the PDF alone and its text is an artifact, on a ${size.width} px screen defect`, async ({ page, api }) => {
    // Defect: a PDF's text stood in a panel stacked under the PDF in the PDF tab. Fix: the tab shows the PDF alone; the
    // text is the Artifacts tab's Text group, searchable as before
    await page.setViewportSize(size);
    const detail = { ...RUN_DETAILS["gateway-review"], pdf_mode: "text-only" as const, video: "inbox/Gateway guide.pdf",
      has_recording: true, recording_parts: [{ name: "Gateway guide.pdf", size_bytes: 797_000, start_s: 0, duration_s: 0 }],
      batches: [{ title: "Gateway setup", start_s: 0, end_s: 60, summary: "" }] };
    api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
    api.answer("GET /runs/output/gateway-review/artifacts", { json: [
      { key: "text", title: "Text", kind: "text", items: [{ path: "01_transcript.json", name: "01_transcript.json", size_bytes: 90 }] }] });
    api.answer("GET /runs/output/gateway-review/transcript", { json: { lines: [
      { start_s: 0, end_s: 60, text: "The router picks the ledger shard from the tenant id." }] } });
    api.answer("GET /runs/output/gateway-review/recording", { body: "%PDF-1.4\n%%EOF\n" });
    await openDoneRun(page);
    await page.getByRole("tab", { name: "PDF" }).click();
    await expect(page.getByText("Gateway guide.pdf").first()).toBeVisible();
    await expect(page.locator(".recording-side")).toHaveCount(0);
    await expect(page.getByRole("tab", { name: "Text" })).toHaveCount(0);
    await page.screenshot({ path: `../temp/pdf-tab-widths/pdf-${size.width}.png`, fullPage: true });
    await page.getByRole("tab", { name: "Artifacts" }).click();
    await page.getByRole("tab", { name: "Text" }).click();
    await expect(page.getByText("The router picks the ledger shard from the tenant id.")).toBeVisible();
    await expect(page.getByRole("combobox", { name: "search the text" })).toBeVisible();
    await expectInsideScreen(page, page.locator(".transcript").first());
    await page.screenshot({ path: `../temp/pdf-tab-widths/text-${size.width}.png`, fullPage: true });
  });
}

for (const size of SCREEN_WIDTHS) {
  test(`each menu's help opens inside the menu, within a ${size.width} px screen defect`, async ({ page, api }) => {
    // Defect: the help stood as a loose "?" in the toolbar, and the panel's top line was oversized and padded
    // Fix: each menu's top line carries its own "?", and its help opens under that line inside the menu
    await page.setViewportSize(size);
    const detail = { ...RUN_DETAILS["gateway-review"], pdf_mode: "markdown-conversion" as const };
    api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
    await openDoneRun(page);
    await expect(page.locator(".head-actions > .dropdown, .head-actions > button")
      .filter({ has: page.getByRole("button", { name: "what these menus do" }) })).toHaveCount(0);
    for (const [menu, line] of [[/Change this document/, /stays in the Open document menu/], [/Copy and run again/, /linked, not copied/]] as const) {
      await page.getByRole("button", { name: menu }).click();
      const panel = page.locator(".dropdown-panel").last();
      await panel.getByRole("button", { name: "explain this menu" }).click();
      await expect(panel.getByText(line)).toBeVisible();
      // the help names what the page shows, never a file or folder of the run (defect: it named a phase folder's file)
      await expect(panel).not.toContainText(/07_reshaping|before\.md|phase 7/);
      await expectInsideScreen(page, panel.locator(".menu-note"));
      // the note's text is the size of every menu's note, never larger
      expect(await panel.locator(".menu-note-line > span").evaluate((node) => getComputedStyle(node).fontSize)).toBe("13px");
      await page.keyboard.press("Escape");
    }
  });
}

const VERSIONED_RUN = { ...RUN_DETAILS["gateway-review"], pdf_mode: "markdown-conversion" as const,
  document_file: "gateway/gateway.md", document_versions: [
    { number: 1, label: "original", path: "07_reshaping/earlier/1/before.md" },
    { number: 2, label: "after clean up", path: "07_reshaping/before.md" },
    { number: 3, label: "after rewrite", path: "gateway/gateway.md" }] };

for (const size of SCREEN_WIDTHS) {
  test(`the document's menu lists each version with its download, and one to download all, on a ${size.width} px screen`, async ({ page, api }) => {
    await page.setViewportSize(size);
    api.answer("GET /runs/output/gateway-review/update", { json: { run: VERSIONED_RUN, offset: 0, text: "" } });
    await openDoneRun(page);
    await page.getByRole("button", { name: "more ways to get the document" }).click();
    const menu = page.locator(".document-versions");
    await expect(menu.getByRole("menuitem")).toHaveText([/1 · original/, /2 · after clean up/,
      /3 · after rewrite\s*the published document/, /Download all versions/]);
    for (const number of [1, 2, 3]) {
      await expectInsideScreen(page, menu.getByRole("button", { name: `download version ${number}` }));
    }
    await expectInsideScreen(page, menu);
    // a version saves alone, as its own zip, and the menu stays open for the next one
    api.answer("GET /runs/output/gateway-review/document-versions/download", {
      body: Buffer.from("PK\u0005\u0006" + "\u0000".repeat(18), "binary"),
      headers: { "Content-Type": "application/zip", "Content-Disposition": "attachment; filename*=UTF-8''gateway-2-after-clean-up.zip" } });
    const saved = page.waitForEvent("download");
    await menu.getByRole("button", { name: "download version 2" }).click();
    expect((await saved).url()).toContain("/document-versions/download?number=2");
    await expect(menu).toBeVisible();
  });
}

test("a version's name opens that version in the reader, its figure read from the published folder", async ({ page, api }) => {
  api.answer("GET /runs/output/gateway-review/update", { json: { run: VERSIONED_RUN, offset: 0, text: "" } });
  api.answer("GET /runs/output/gateway-review/artifacts", { json: [
    { key: "cleanup", title: "Clean up", kind: "markdown", items: [
      { path: "07_reshaping/earlier/1/before.md", name: "before.md", size_bytes: 60 }] }] });
  api.answer("GET /runs/output/gateway-review/files/07_reshaping/earlier/1/before.md",
    { body: "# Gateway rollout plan\n\nThe first version.\n\n![the cache](resources/figure-01.png)\n" });
  const figureAsked = page.waitForRequest((request) => request.url().endsWith("/files/gateway/resources/figure-01.png"));
  api.answer("GET /runs/output/gateway-review/files/gateway/resources/figure-01.png", {
    body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=", "base64"),
    headers: { "Content-Type": "image/png" } });
  await openDoneRun(page);
  await page.getByRole("button", { name: "more ways to get the document" }).click();
  await page.getByRole("menuitem", { name: /1 · original/ }).click();
  await expect(page.getByText("The first version.")).toBeVisible();
  await figureAsked;
});

const KNOWLEDGE_DRAFTS_RUN = { ...RUN_DETAILS["gateway-review"], pdf_mode: "markdown-conversion" as const,
  document_file: "gateway/gateway.md", document_versions: [
    { number: 1, label: "original", path: "07_reshaping/before.md" },
    { number: 2, label: "knowledge draft after part 1 of 3", path: "07_reshaping/parts/part-001.md" },
    { number: 3, label: "knowledge draft after part 2 of 3", path: "07_reshaping/parts/part-002.md" },
    { number: 4, label: "after knowledge (not finished)", path: "gateway/gateway.md" }] };

for (const size of SCREEN_WIDTHS) {
  test(`a running knowledge pass lists its draft after each part, each opened by its name, on a ${size.width} px screen`, async ({ page, api }) => {
    // The owner: "the UI should show the draft for the final document ... after each part"
    await page.setViewportSize(size);
    api.answer("GET /runs/output/gateway-review/update", { json: { run: KNOWLEDGE_DRAFTS_RUN, offset: 0, text: "" } });
    api.answer("GET /runs/output/gateway-review/artifacts", { json: [
      { key: "cleanup", title: "Clean up", kind: "markdown", items: [
        { path: "07_reshaping/parts/part-002.md", name: "part-002.md", size_bytes: 60 }] }] });
    api.answer("GET /runs/output/gateway-review/files/07_reshaping/parts/part-002.md",
      { body: "# Gateway rollout plan\n\n## Refund limits\n\nThe draft after part 2.\n" });
    await openDoneRun(page);
    await page.getByRole("button", { name: "more ways to get the document" }).click();
    const menu = page.locator(".document-versions");
    await expect(menu.getByRole("menuitem")).toHaveText([/1 · original/, /2 · knowledge draft after part 1 of 3/,
      /3 · knowledge draft after part 2 of 3/, /4 · after knowledge \(not finished\)\s*the published document/,
      /Download all versions/]);
    await expectInsideScreen(page, menu);
    await expectInsideScreen(page, menu.getByRole("button", { name: "download version 3" }));
    await menu.getByRole("menuitem", { name: /3 · knowledge draft after part 2 of 3/ }).click();
    await expect(page.getByText("The draft after part 2.")).toBeVisible();
  });
}

for (const size of SCREEN_WIDTHS) {
  test(`a document no pass changed is listed as its one version with its download on its row, on a ${size.width} px screen`, async ({ page, api }) => {
    await page.setViewportSize(size);
    const detail = { ...RUN_DETAILS["gateway-review"], document_file: "gateway/gateway.md",
      document_versions: [{ number: 1, label: "original", path: "gateway/gateway.md" }] };
    api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
    await openDoneRun(page);
    await page.getByRole("button", { name: "more ways to get the document" }).click();
    const menu = page.locator(".document-versions");
    // one row, no "Download all versions" for a single document
    await expect(menu.getByRole("menuitem")).toHaveText([/1 · original\s*the published document/]);
    const row = menu.locator(".version-row");
    const opened = await row.getByRole("menuitem").boundingBox();
    const download = await row.getByRole("button", { name: "download version 1" }).boundingBox();
    expect(Math.abs((download!.y + download!.height / 2) - (opened!.y + opened!.height / 2))).toBeLessThan(8);
    await expectInsideScreen(page, menu);
  });
}

test("a server that lists no version keeps the one Download document item", async ({ page, api }) => {
  const detail = { ...RUN_DETAILS["gateway-review"], document_file: "gateway/gateway.md", document_versions: [] };
  api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
  await openDoneRun(page);
  await page.getByRole("button", { name: "more ways to get the document" }).click();
  await expect(page.getByRole("menuitem")).toHaveText([/Download document/]);
});

test("a done audio run writes its document again in a profile that reads the same notes, in this run", async ({ page, api }) => {
  const detail = { ...RUN_DETAILS["gateway-review"], is_audio: true, appendable_profiles: ["knowledge", "meeting"] };
  api.answer("GET /runs/output/gateway-review/update", { json: { run: detail, offset: 0, text: "" } });
  api.answer("POST /runs/output/gateway-review/convert", { json: { exit_code: 0, output: "set aside",
    records: [{ folder: "/work/output/gateway-review" }], jobs: [{ job_id: "j9" }] } });
  await openDoneRun(page);
  for (const size of SCREEN_WIDTHS) {
    await page.setViewportSize(size);
    await page.getByRole("button", { name: /Change this document/ }).click();
    await expect(page.getByRole("menuitem")).toHaveText([/Write as knowledge/, /Write as meeting/]);
    await expectInsideScreen(page, page.getByRole("menu"));
    await page.keyboard.press("Escape");
  }
  await page.getByRole("button", { name: /Change this document/ }).click();
  await page.getByRole("menuitem", { name: /Write as meeting/ }).click();
  await expect(page.getByText(/gateway-review is written again as meeting/)).toBeVisible();
  expect(api.sentTo("POST /runs/output/gateway-review/convert")[0].body).toEqual({ profile: "meeting", is_in_place: true });
});

test("a done video run offers no clean up or rewrite", async ({ page }) => {
  await openDoneRun(page);
  await expect(page.getByRole("button", { name: /Change this document/ })).toHaveCount(0);
});
