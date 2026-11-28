// The inbox and its run list: the lists by state, a run folder no inbox video owns, processing a ticked video, and
// opening a run from its row.
import * as data from "./fixtures/data";
import { JOBS } from "./fixtures/data";
import { expectInsideScreen, SCREEN_WIDTHS } from "./fixtures/layout";
import { expect, test } from "./fixtures/mockApi";

const CHANGE_REQUEST = { ...JOBS[0].request, document_pass: "knowledge", from_phase: 7 };

for (const size of SCREEN_WIDTHS) {
  test.describe(`the upload box at ${size.width} px`, () => {
    // a phone cannot drag files (the owner: "on phone you can't drag and drop files"): a touch screen is told to tap
    const isTouch = size.width < 900;
    test.use({ viewport: size, hasTouch: isTouch, isMobile: isTouch });
    test(`says ${isTouch ? "tap to choose" : "drop or browse"} defect`, async ({ page }) => {
      await page.goto("/");
      const zone = page.getByRole("button", { name: /recordings/ }).first();
      await expect(zone.getByText("Tap to choose recordings")).toBeVisible({ visible: isTouch });
      await expect(zone.getByText("Drop recordings")).toBeVisible({ visible: !isTouch });
      await expectInsideScreen(page, zone);
    });
  });
}

for (const size of SCREEN_WIDTHS) {
  test(`the run settings for new recordings pick a provider set first in their Settings tab, named on the tab, at ${size.width} px`,
    async ({ page, api }) => {
      // the owner: "add provider selection for batch run config"; it sat under the tab's other fields, its name cut off
      await page.setViewportSize(size);
      api.answer("GET /provider-sets", { json: { default_set: "default", sets: [{ name: "default", changes: [] },
        { name: "gpu-box", changes: [] }] } });
      await page.goto("/");
      await page.getByRole("button", { name: "run settings" }).click();
      const settingsTab = page.getByRole("tab", { name: /Settings/ });
      await settingsTab.click();
      const choice = page.getByRole("combobox", { name: "Provider set" });
      await expectInsideScreen(page, choice);
      await choice.selectOption("gpu-box");
      await expect(settingsTab).toContainText(/^Settings\s*provider set: gpu-box/);
    });
}

for (const size of SCREEN_WIDTHS) {
  test(`the run settings' Video tab starts every new video fast, and a video's own page starts from it, at ${size.width} px defect`,
    async ({ page, api }) => {
      // Defect: the fast settings chosen in the inbox's run settings never reached a video's page, which read "Default:
      // Quality", and several videos could not be given the fast workflow at once. Fix: a Video tab, kept for the page
      await page.setViewportSize(size);
      await page.goto("/");
      await page.getByRole("button", { name: "run settings" }).click();
      const dialog = page.getByRole("dialog", { name: /Run settings/ });
      await dialog.getByRole("tab", { name: /^Video/ }).click();
      await dialog.getByRole("button", { name: "workflow speed: Quality" }).click();
      // the Workflow speed menu, opened: its presets and its settings, inside the screen
      const menu = page.locator(".speed-panel");
      await expectInsideScreen(page, menu);
      await menu.getByRole("radio", { name: /^Fast/ }).click();
      // Escape closes the menu alone, never the dialog round it (defect: it closed both)
      await page.keyboard.press("Escape");
      await expect(menu).toHaveCount(0);
      await expect(dialog).toBeVisible();
      await expect(dialog.getByRole("tab", { name: /^Video/ })).toContainText("speed Fast");
      await dialog.locator(".run-settings-actions .btn.primary").click();
      await expect.poll(() => api.sentTo("POST /jobs").length).toBe(1);
      expect(api.sentTo("POST /jobs")[0].body).toMatchObject({ speed_preset: "fast" });
      await page.reload();
      await page.getByRole("button", { name: "new ›" }).click();
      await expect(page.getByRole("button", { name: "workflow speed: Fast" })).toBeVisible();
    });

  test(`a list shows one type once a row is ticked, and its videos alone are ticked and processed, at ${size.width} px`,
    async ({ page, api }) => {
      await page.setViewportSize(size);
      const guide = { ...data.INBOX[0], name: "Gateway guide.pdf", video: "inbox/Gateway guide.pdf", path: "inbox/Gateway guide.pdf",
        output: "output/Gateway guide", file_type: "pdf" };
      api.answer("GET /inbox", { json: [{ ...data.INBOX[0], file_type: "video" }, guide, ...data.INBOX.slice(1)] });
      await page.goto("/");
      await expect(page.getByRole("radiogroup", { name: /show one type/ })).toHaveCount(0);
      await page.getByRole("checkbox", { name: "tick Gateway guide.pdf" }).check();
      const bar = page.getByRole("radiogroup", { name: "show one type in New" });
      await expect(bar.getByRole("radio")).toHaveText(["All 2", "Videos 1", "PDFs 1"]);
      await expectInsideScreen(page, bar);
      await bar.getByRole("radio", { name: /Videos/ }).click();
      await expect(page.getByRole("checkbox", { name: "tick Gateway guide.pdf" })).toHaveCount(0);
      await bar.getByRole("button", { name: "Tick only the 1" }).click();
      await expect(page.getByRole("checkbox", { name: "tick AcmeSync kickoff.mp4" })).toBeChecked();
      await bar.getByRole("radio", { name: /All/ }).click();
      // the PDF ticked before is unticked: the list's videos alone go to the queue
      await expect(page.getByRole("checkbox", { name: "tick Gateway guide.pdf" })).not.toBeChecked();
      // the whole list ticked, then Videos chosen: the hidden PDF is unticked too (it was processed with the videos)
      await page.getByRole("checkbox", { name: "select every video in New" }).check();
      await bar.getByRole("radio", { name: /Videos/ }).click();
      await page.getByRole("button", { name: /^Process 1 video/ }).click();
      await expect.poll(() => api.sentTo("POST /jobs").length).toBe(1);
      expect(api.sentTo("POST /jobs")[0].body).toMatchObject({ items: [{ video: "inbox/AcmeSync kickoff.mp4" }] });
    });

  test(`a video's page names the speed the settings in force hold, at ${size.width} px defect`, async ({ page, api }) => {
    // Defect: the fast settings chosen, a video's page read "Default: Quality". Fix: the page names the preset the
    // settings in force hold
    await page.setViewportSize(size);
    api.answer("GET /run-defaults", { json: { ...data.RUN_DEFAULTS,
      speed: { ...data.RUN_DEFAULTS.speed, default_preset: "fast" } } });
    await page.goto("/");
    await page.getByRole("button", { name: "new ›" }).click();
    await expect(page.getByRole("button", { name: "workflow speed: Fast" })).toBeVisible();
  });

  test(`a done run whose change failed reads change failed at ${size.width} defect`, async ({ page, api }) => {
    // A done run queued again to change its document kept reading done when its new job failed; its row now shows
    // the last state alone, and opening it still works.
    await page.setViewportSize(size);
    api.answer("GET /jobs", { json: [JOBS[0], { ...JOBS[0], job_id: "job-change", status: "crashed", exit_code: 1, request: CHANGE_REQUEST }] });
    await page.goto("/");
    await page.getByRole("button", { name: /^Done\s*2/ }).click();
    const pill = page.getByRole("button", { name: "change failed › open the run of Gateway review.mp4" });
    await expect(pill).toBeVisible();
    await expect(page.getByRole("button", { name: /^done › open the run of Gateway review\.mp4/ })).toHaveCount(0);
    await expectInsideScreen(page, pill);
    await pill.click();
    await expect(page.getByRole("heading", { name: "gateway-review", level: 2 })).toBeVisible();
  });
}

for (const [status, word] of [["stopped", "change stopped"], ["incomplete", "change failed"], ["refused", "change failed"]] as const) {
  test(`a done run's change that ended ${status} reads ${word}`, async ({ page, api }) => {
    api.answer("GET /jobs", { json: [{ ...JOBS[0], job_id: "job-change", status, request: CHANGE_REQUEST }] });
    await page.goto("/");
    await page.getByRole("button", { name: /^Done\s*2/ }).click();
    await expect(page.getByRole("button", { name: `${word} › open the run of Gateway review.mp4` })).toBeVisible();
  });
}

for (const [runStatus, word] of [["failed", "change failed"], ["complete", "done"]] as const) {
  test(`a change whose job ended unseen reads ${word} when its run is ${runStatus} defect`, async ({ page, api }) => {
    // A knowledge pass stopped at part 16 of 21, its job ended "lost" (no server saw its end), and the row read done
    // while the run's header read failed. Now such a change takes the run's own status
    // the video's row reads its run from the inbox list
    const review = data.INBOX[1];
    api.answer("GET /inbox", { json: [data.INBOX[0], { ...review, runs: [{ ...review.runs[0], status: runStatus }] },
      ...data.INBOX.slice(2)] });
    api.answer("GET /jobs", { json: [{ ...JOBS[0], job_id: "job-change", status: "lost", request: CHANGE_REQUEST }] });
    await page.goto("/");
    await page.getByRole("button", { name: /^Done\s*2/ }).click();
    await expect(page.getByRole("button", { name: `${word} › open the run of Gateway review.mp4` })).toBeVisible();
  });
}

test("a done run whose last job finished reads done", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /^Done\s*2/ }).click();
  await expect(page.getByRole("button", { name: /done.*open the run of Gateway review\.mp4/ })).toBeVisible();
});

test("lists the videos by state, the done list closed until opened", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("button", { name: /^New\s*1/ })).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByText("AcmeSync kickoff.mp4")).toBeVisible();
  await expect(page.getByRole("button", { name: /^Not finished\s*1/ })).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByText("Jonas onboarding.m4a")).toBeVisible();
  // a started run's pill names the phase it stopped in
  await expect(page.getByRole("button", { name: "phase 3 ›" })).toBeVisible();

  const done = page.getByRole("button", { name: /^Done\s*2/ });
  await expect(done).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByText("Gateway review.mp4")).toHaveCount(0);
  await done.click();
  await expect(page.getByText("Gateway review.mp4")).toBeVisible();
  await expect(page.getByText("Gateway rollout plan")).toBeVisible();
  // the run folder whose recording is in no inbox list stands in the done list by itself, under its topic
  await expect(page.getByRole("button", { name: /acmesync-retro/ })).toBeVisible();
  await expect(page.getByText("AcmeSync sprint retrospective")).toBeVisible();
});

for (const width of [390, 768, 1100, 1440]) {
  test(`a done run folder's row shows its recording's size, inside the screen at ${width} px defect`, async ({ page }) => {
    // A done run's row showed only its file type, since its recording had moved into the run folder; it now shows
    // the size of the recording kept there
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await page.getByRole("button", { name: /^Done\s*2/ }).click();
    const line = page.getByRole("button", { name: /acmesync-retro/ }).locator(".row-main small.muted");
    await expect(line).toHaveText("pdf28 MB");
    const box = (await line.boundingBox())!;
    expect(box.x, "starts on the screen").toBeGreaterThanOrEqual(0);
    expect(box.x + box.width, "ends on the screen").toBeLessThanOrEqual(width);
  });
}

for (const width of [390, 768, 1100, 1440]) {
  test(`a ticked PDF run folder is named in the run settings, with its PDF tab, at ${width} px defect`, async ({ page, api }) => {
    // Defect: a ticked done PDF run read "every new recording", showed no PDF tab and a disabled "No video ticked".
    // Fix: it is a row Process queues in its own folder; the Models tab's workflows fold, closed at first
    const retro = data.RUNS.find((run) => run.name === "acmesync-retro")!;
    api.answer("GET /runs", { json: data.RUNS.map((run) => run === retro ? { ...run, video: "inbox/AcmeSync retro.pdf" } : run) });
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await page.getByRole("button", { name: /^Done\s*2/ }).click();
    await page.getByRole("checkbox", { name: "tick acmesync-retro" }).check();
    await page.getByRole("button", { name: "run settings" }).click();
    const dialog = page.getByRole("dialog", { name: /Run settings · 1 PDF ticked/ });
    await expect(dialog.getByRole("tab", { name: /^PDF/ })).toBeVisible();
    await dialog.getByRole("tab", { name: /^Models/ }).click();
    const fold = dialog.locator(".workflow-models details").first();
    await expect(fold).not.toHaveAttribute("open", "");
    await fold.locator("summary").click();
    await expect(fold.getByRole("combobox").first()).toBeVisible();
    await expectInsideScreen(page, dialog.locator(".workflow-models").first());
    await page.screenshot({ path: `../temp/ticked-pdf-run-widths/models-${width}.png` });
    await dialog.getByRole("button", { name: "Process 1 PDF" }).click();
    // a done run is asked about first, as a done inbox recording is
    await page.getByRole("dialog", { name: /Process done recordings again/ }).getByRole("button", { name: "Process again" }).click();
    await expect.poll(() => api.sentTo("POST /jobs").length).toBe(1);
    expect(api.sentTo("POST /jobs")[0].body).toMatchObject({ items: [{ video: "inbox/AcmeSync retro.pdf", output: retro.folder }] });
  });
}

for (const width of [390, 768, 1100, 1440]) {
  test(`the ticked finished PDFs' documents are changed by phase 7 alone, never run from the start, at ${width} px`, async ({ page, api }) => {
    // The owner: "i want to be able to change the final document in batches ... i dont want to run selected pdf from
    // the start"; the run settings' PDF tab queues the chosen pass over each ticked document, in its own run folder
    const retro = data.RUNS.find((run) => run.name === "acmesync-retro")!;
    api.answer("GET /runs", { json: data.RUNS.map((run) => run === retro
      ? { ...run, video: "inbox/AcmeSync retro.pdf", document_pass: "knowledge" } : run) });
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await page.getByRole("button", { name: /^Done\s*2/ }).click();
    await page.getByRole("checkbox", { name: "tick acmesync-retro" }).check();
    await page.getByRole("button", { name: "run settings" }).click();
    const dialog = page.getByRole("dialog", { name: /Run settings/ });
    await dialog.getByRole("tab", { name: /^PDF/ }).click();
    const change = dialog.getByRole("region", { name: "change the ticked documents" });
    await expect(change.getByRole("heading", { name: "Change the 1 ticked document" })).toBeVisible();
    // a drop-down list like the dialog's other choices, each option with its description, its help beside it
    const changeList = change.getByRole("combobox", { name: "Change" });
    await expect(changeList.locator("option")).toHaveText(["choose how to change the documents",
      "clean up with LLM: removes noise, keeps every sentence", "rewrite with LLM: better structure and writing",
      "knowledge reshaping with LLM: reshapes the document into a knowledge-focused document, in parts, never the whole "
        + "document at once"]);
    await changeList.selectOption("rewrite");
    await change.getByRole("button", { name: "what Change means" }).click();
    await expect(change.getByRole("note", { name: "Change explained" })).toContainText("You get: the same facts, better written");
    // the run had a knowledge reshaping, so it asks what the rewrite starts from
    await change.getByRole("combobox", { name: "Start from" }).selectOption("current");
    await expectInsideScreen(page, change);
    await page.screenshot({ path: `../temp/change-documents-widths/change-${width}.png` });
    await change.getByRole("button", { name: "Queue the rewrite" }).click();
    await expect(page.getByText("Queued a rewrite of 1 document")).toBeVisible();
    expect(api.sentTo("POST /jobs")[0].body).toMatchObject({ items: [{ video: "inbox/AcmeSync retro.pdf",
      output: retro.folder }], only_phase: 7, is_resuming: true, document_pass: "rewrite", document_source: "current" });
  });
}

test("processes a ticked video and says it waits behind the queued job", async ({ page, api }) => {
  await page.goto("/");

  // with nothing ticked the one Process button names what it would queue: every new video
  await expect(page.getByRole("button", { name: "Process all new (1)" })).toBeEnabled();
  await page.getByRole("checkbox", { name: /AcmeSync kickoff\.mp4/ }).check();
  await page.getByRole("button", { name: "Process 1 video" }).click();

  await expect(page.getByText("Queued AcmeSync kickoff.mp4 behind 1 job")).toBeVisible();
  const sent = api.sentTo("POST /jobs");
  expect(sent).toHaveLength(1);
  expect(sent[0].body).toMatchObject({
    items: [{ video: "inbox/AcmeSync kickoff.mp4", output: "output/AcmeSync kickoff" }],
    only_phase: null, is_dry_run: false,
  });
});

test("the phases menu changes what the process button runs", async ({ page, api }) => {
  await page.goto("/");

  // the phases, settings and dry run sit behind the Process button's gear, a tab each, each choice a drop-down list
  await page.getByRole("button", { name: "run settings" }).click();
  await page.getByRole("radio", { name: "From" }).click();
  await page.getByRole("combobox", { name: "Phase", exact: true }).selectOption("4");
  await page.getByRole("tab", { name: /^Settings/ }).click();
  await page.getByText("prompts only, no model call").click();
  await expect(page.getByRole("checkbox", { name: /Dry run/ })).toBeChecked();

  await page.keyboard.press("Escape");
  // a choice other than the default shows on the Process button itself
  await expect(page.getByRole("button", { name: /^Process all new \(1\)/ })).toContainText("from 4 · dry run");
  await page.getByRole("checkbox", { name: /AcmeSync kickoff\.mp4/ }).check();
  await page.getByRole("button", { name: /^Process 1 video/ }).click();
  await expect(page.getByText(/Queued AcmeSync kickoff\.mp4/)).toBeVisible();
  expect(api.sentTo("POST /jobs")[0].body).toMatchObject({ from_phase: 4, only_phase: null, is_dry_run: true });
});

test("a list's download is active only while every ticked row of it is done, and saves those as one zip defect", async ({ page, api }) => {
  // The button showed with nothing ticked and downloaded every done row unasked; then it was hidden instead. Now it is
  // always there, inactive while nothing is ticked or a ticked row is not done (the owner: "make it inactive").
  api.answer("GET /lists", { json: { lists: [{ id: "a", name: "Review later",
    members: ["inbox/Gateway review.mp4", "inbox/Jonas onboarding.m4a"] }] } });
  await page.goto("/");
  const download = page.getByRole("button", { name: "download from the list Review later" });
  const list = page.getByRole("button", { name: /^Review later/ });
  if (await list.getAttribute("aria-expanded") !== "true") await list.click();
  await expect(page.getByText("Gateway review.mp4")).toBeVisible();
  await expect(download).toBeDisabled();

  await page.getByRole("checkbox", { name: /Gateway review\.mp4/ }).check();
  await expect(download).toBeEnabled();
  // one ticked row not done makes it inactive, never hides it
  await page.getByRole("checkbox", { name: /Jonas onboarding\.m4a/ }).check();
  await expect(download).toBeDisabled();
  await page.getByRole("checkbox", { name: /Jonas onboarding\.m4a/ }).uncheck();

  const saved = page.waitForEvent("download");
  await download.click();
  expect((await saved).suggestedFilename()).toBe("documents.zip");
  expect(api.sentTo("POST /download/documents")[0].body).toEqual({
    runs: [{ root: "output", name: "gateway-review" }], parts: ["document"], version: "" });
});

test("a list's run started again from the recording it keeps stays in its list, never in Running defect", async ({ page, api }) => {
  // The defect: a done run of Review later run again from phase 3 read its video from its own folder's copy, and the
  // row left Review later for Running (the owner). The fix: the server names the inbox path the copy came from.
  api.answer("GET /lists", { json: { lists: [{ id: "a", name: "Review later", members: ["inbox/Gateway review.mp4"] }] } });
  api.answer("GET /inbox", { json: data.INBOX.filter((item) => item.video !== "inbox/Gateway review.mp4") });
  api.answer("GET /runs", { json: data.RUNS.map((run) => run.name !== "gateway-review" ? run : { ...run, is_live: true,
    status: "running", video: "output/gateway-review/recording/Gateway review.mp4", origin_video: "inbox/Gateway review.mp4" }) });
  await page.goto("/");
  const section = (title: string) => page.locator(".inbox-section", { has: page.getByLabel(`${title}: drop a row here`) });
  const list = page.getByRole("button", { name: /^Review later/ });
  if (await list.getAttribute("aria-expanded") !== "true") await list.click();
  await expect(section("Review later").getByRole("checkbox", { name: "tick gateway-review" })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "tick gateway-review" })).toHaveCount(1);
});

for (const size of SCREEN_WIDTHS) {
  test(`a run paused on a second machine says paused with its set on its pill, at ${size.width} px defect`,
    async ({ page, api }) => {
      // Defect: the owner paused a run on a second machine, it held, and its pill still said running. Fix: the run
      // list says which live run holds at its pause, and its pill reads "paused · gpu-box"
      await page.setViewportSize(size);
      api.answer("GET /inbox", { json: data.INBOX.filter((item) => item.video !== "inbox/Gateway review.mp4") });
      api.answer("GET /runs", { json: data.RUNS.map((run) => run.name === "gateway-review"
        ? { ...run, is_live: true, status: "running", provider_set: "gpu-box", pause: "paused" }
        : run.name === "jonas-onboarding" ? { ...run, is_live: true, status: "running", provider_set: "Office", pause: "" } : run) });
      await page.goto("/");
      const paused = page.locator(".pill", { hasText: "paused · gpu-box" });
      await expect(paused).toBeVisible();
      await expect(page.locator(".pill", { hasText: "running · gpu-box" })).toHaveCount(0);
      await expect(paused).toHaveClass(/held/);
      await expectInsideScreen(page, paused);
      await page.screenshot({ path: `../temp/paused-pill/paused-${size.width}.png`, fullPage: true });
    });
}

for (const size of SCREEN_WIDTHS) {
  test(`a list's rows stand by their status, a queued change first and done at the bottom, at ${size.width} px`, async ({ page, api }) => {
    await page.setViewportSize(size);
    api.answer("GET /lists", { json: { lists: [{ id: "a", name: "Review later",
      members: ["inbox/AcmeSync kickoff.mp4", "inbox/Gateway review.mp4", "inbox/Jonas onboarding.m4a"] }] } });
    api.answer("GET /jobs", { json: [{ ...JOBS[0], job_id: "job-change", status: "queued", request: CHANGE_REQUEST }] });
    await page.goto("/");
    const list = page.getByRole("button", { name: /^Review later/ });
    if (await list.getAttribute("aria-expanded") !== "true") await list.click();
    const section = page.locator(".section-list");
    await expect(section.locator(".row-name")).toHaveText(["Gateway review.mp4", "Jonas onboarding.m4a", "AcmeSync kickoff.mp4"]);
    await expectInsideScreen(page, section);
  });
}

for (const width of [390, 768, 1100, 1440]) {
  test(`the Done section has the download button too, New and Not finished none, at ${width} px`, async ({ page, api }) => {
    // The owner: "I selected all elements in the done list, but the download button is not there"; and New and Not
    // finished never have it, since no row there is done
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await expect(page.getByRole("button", { name: "download from the list New" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "download from the list Not finished" })).toHaveCount(0);
    await page.getByRole("button", { name: /^Done\s*2/ }).click();
    const download = page.getByRole("button", { name: "download from the list Done" });
    await expect(download).toBeDisabled();
    await page.getByRole("checkbox", { name: /Gateway review\.mp4/ }).check();
    await page.getByRole("checkbox", { name: "tick acmesync-retro" }).check();
    await expect(download).toBeEnabled();
    await expectInsideScreen(page, download);
    await page.screenshot({ path: `../temp/list-download-widths/done-${width}.png` });
    const saved = page.waitForEvent("download");
    await download.click();
    await saved;
    expect(api.sentTo("POST /download/documents")[0].body).toMatchObject({ runs: [
      { root: "output", name: "gateway-review" }, { root: "output", name: "acmesync-retro" }] });
  });
}

for (const width of [390, 768, 1100, 1440]) {
  test(`a list's download asks which version when a ticked document has several, at ${width} px`, async ({ page, api }) => {
    // The owner: the download gave one document per run with no word of which version; a run with several versions
    // (the original and a reshaping) now asks: the latest, the original or all of them
    await page.setViewportSize({ width, height: 900 });
    api.answer("GET /lists", { json: { lists: [{ id: "a", name: "Review later", members: ["inbox/Gateway review.mp4"] }] } });
    api.answer("POST /download/document-versions", { json: [{ root: "output", name: "gateway-review", versions: [
      { number: 1, label: "markdown conversion (original)", path: "07_reshaping/before.md" },
      { number: 2, label: "after knowledge reshaping (not finished)", path: "gateway/gateway.md" }] }] });
    await page.goto("/");
    const list = page.getByRole("button", { name: /^Review later/ });
    if (await list.getAttribute("aria-expanded") !== "true") await list.click();
    await page.getByRole("checkbox", { name: /Gateway review\.mp4/ }).check();
    await page.getByRole("button", { name: "download from the list Review later" }).click();
    const dialog = page.getByRole("dialog", { name: "Which version do you want to download?" });
    // a card a document with its versions as tags, never one comma line (the owner: "no one can read those, they
    // should be elements with details")
    const tags = dialog.getByRole("list", { name: "versions of gateway-review" }).getByRole("listitem");
    await expect(tags).toHaveText(["markdown conversion (original)", /after knowledge reshaping\s*not finished/]);
    await expect(tags.nth(1)).toHaveClass(/unfinished/);
    await expect(dialog.getByText("2 versions")).toBeVisible();
    await expect(dialog).not.toContainText("gateway-review: ");
    await expectInsideScreen(page, dialog);
    await expectInsideScreen(page, tags.nth(1));
    for (const name of ["Cancel", "The original", "All versions", "The latest"]) {
      await expectInsideScreen(page, dialog.getByRole("button", { name, exact: true }));
    }
    await page.screenshot({ path: `../temp/download-versions-widths/ask-${width}.png` });
    const saved = page.waitForEvent("download");
    await dialog.getByRole("button", { name: "All versions" }).click();
    await saved;
    expect(api.sentTo("POST /download/documents")[0].body).toMatchObject({ version: "all" });
  });
}

test("a ticked recording already running with the same settings is left out of Process defect", async ({ page, api }) => {
  // Ticked with others while it ran, a recording was queued a second time and its row read "queued". Now it is left
  // out and named while its run's settings are the ones asked.
  const running = { ...JOBS[1], job_id: "job-running", status: "running" as const, pid: 77,
    request: { ...JOBS[1].request, video: "inbox/Jonas onboarding.m4a", output_root: "output/jonas-onboarding" } };
  api.answer("GET /jobs", { json: [...JOBS, running] });
  await page.goto("/");

  await page.getByRole("checkbox", { name: /AcmeSync kickoff\.mp4/ }).check();
  await page.getByRole("checkbox", { name: /Jonas onboarding\.m4a/ }).check();
  await page.getByRole("button", { name: /^Process 2 recordings/ }).click();

  await expect(page.getByText(/Jonas onboarding\.m4a left out: already running with the same settings/)).toBeVisible();
  const queued = api.sentTo("POST /jobs").flatMap((sent) => (sent.body as { items: { video: string }[] }).items);
  expect(queued.map((item) => item.video)).toEqual(["inbox/AcmeSync kickoff.mp4"]);
});

test("a click on a done video's name opens its run in the detail view", async ({ page }) => {
  await page.goto("/");

  await page.getByRole("button", { name: /^Done\s*2/ }).click();
  await page.getByText("Gateway review.mp4").click();

  await expect(page.getByRole("heading", { name: "gateway-review", level: 2 })).toBeVisible();
  await page.getByRole("button", { name: "back" }).click();
  await expect(page.getByRole("heading", { name: "Nothing open" })).toBeVisible();
});

for (const size of SCREEN_WIDTHS) {
  test(`a new video's queued pill opens the video's page, never the job console alone, at ${size.width} px defect`,
    async ({ page, api }) => {
      // Defect: a queued new video's pill opened the job's console, which shows neither the video nor what the run made.
      // Fix: the pill opens the video's own page; only a job whose run folder is gone still opens its console
      await page.setViewportSize(size);
      api.answer("GET /jobs", { json: [{ ...JOBS[1], job_id: "job-queued",
        request: { ...JOBS[1].request, video: "inbox/AcmeSync kickoff.mp4", output_root: "output/AcmeSync kickoff" } }] });
      await page.goto("/");
      const pill = page.getByTitle("job job-queued waits: open this video's page");
      await expectInsideScreen(page, pill);
      await pill.click();
      await expect(page.getByRole("heading", { name: "AcmeSync kickoff.mp4", level: 2 })).toBeVisible();
      await expect(page.getByText("Job console")).toHaveCount(0);
    });
}
