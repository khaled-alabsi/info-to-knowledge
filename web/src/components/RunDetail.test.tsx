import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Job, RunDetail as Detail } from "../api";
import { FakeUpdateSource, PHASES, answerJson, answerText, installServer, makeJob, makePhaseRows, makeRun, makeRunDetail } from "../test/fakeServer";
import { SPEED_OPTIONS } from "../test/speedOptions";
import { readViewState } from "../viewState";
import RunDetail from "./RunDetail";

const RUN_BASE = "/api/runs/knowledge/AcmeSync-demo";

function renderRun({ jobs = [] as Job[], run = makeRun() } = {}) {
  const onBack = vi.fn();
  const onChanged = vi.fn();
  const onMessage = vi.fn();
  const view = render(<RunDetail run={run} phases={PHASES} jobs={jobs} onBack={onBack} onChanged={onChanged} onMessage={onMessage} />);
  return { ...view, onBack, onChanged, onMessage };
}

function sendUpdate(detail: Partial<Detail>, text = "", offset = 0) {
  act(() => FakeUpdateSource.latest().emit({ run: makeRunDetail(detail), text, offset }));
}

// The run's actions: beside the title once the run is finished (`.head-actions`), else in the toolbar; the toolbar's
// state notes (a compression, a queued job) stay in it
function toolbar(): HTMLElement {
  const holder = document.createElement("div");
  const parts = [".head-actions", ".toolbar"].map((selector) => document.querySelector<HTMLElement>(selector)).filter(Boolean);
  return parts.length === 1 ? parts[0]! : (document.querySelector<HTMLElement>(".detail") ?? holder);
}

const PROVIDER_SETS = { default_set: "default", sets: [{ name: "default", changes: [], machine: "main machine" },
  { name: "Mac", changes: ["models"], machine: "laptop" }, { name: "gpu-box", changes: ["models"], machine: "gpu-box" }] };

function lastJobRequest(calls: { url: string; body: unknown }[]) {
  return calls.filter((call) => call.url === "/api/jobs").at(-1)?.body;
}

describe("RunDetail before the first update", () => {
  it("shows the folder from the list, follows its event stream, and offers nothing to start", async () => {
    const { onBack } = renderRun();
    expect(FakeUpdateSource.latest().url).toBe(`${RUN_BASE}/update?offset=0`);
    expect(screen.getByRole("heading", { level: 2, name: "AcmeSync-demo" })).toBeInTheDocument();
    expect(screen.getByText("knowledge/AcmeSync-demo · 20261001-120000")).toBeInTheDocument();
    expect(within(document.querySelector(".head-pills")!).getByText("not finished")).toBeInTheDocument();
    expect(toolbar()).toBeEmptyDOMElement();
    // before the first update nothing is known about the log, so the page says it is loading, never that there is none
    expect(screen.getByText("Loading the log …")).toBeInTheDocument();
    expect(screen.queryByText("No log yet.")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "back" }));
    expect(onBack).toHaveBeenCalled();
  });
});

describe("RunDetail action bar", () => {
  it("live: names the running phase and offers only Control, with Interrupt now off for a run not started here", async () => {
    renderRun();
    sendUpdate({ live_pids: [4242], status: "running", phases: makePhaseRows(["complete", "complete", "running", "not run", "not run"]) });
    // "running" says it already: no "live" pill beside it
    expect(screen.queryByText("live")).not.toBeInTheDocument();
    expect(within(document.querySelectorAll(".stepper .step")[3] as HTMLElement).getByText("pending")).toBeInTheDocument();
    expect(screen.queryByText("not run")).not.toBeInTheDocument();
    expect(within(toolbar()).getByText("Running phase 3 · frames processing")).toBeInTheDocument();
    expect(within(toolbar()).queryByText(/Resume|Unblock|Run again/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "more actions" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Control" }));
    expect(screen.getAllByRole("menuitem").map((item) => item.querySelector("span span")?.textContent))
      .toEqual(["Pause", "Drain", "Stop", "Interrupt now"]);
    expect(screen.getByRole("menuitem", { name: /Interrupt now/ })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("menuitem", { name: /Interrupt now/ })).toHaveTextContent("only for a run started from this page");
  });

  it("names each phase by the title the run gives it, so an audio run's phase 2 is speech preparing, help included", async () => {
    renderRun();
    const rows = makePhaseRows(["complete", "running", "not run", "not run", "not run", "not run"]);
    rows[1] = { ...rows[1], title: "speech preparing", summary: "One model call turns the transcript into paragraphs." };
    rows[2] = { ...rows[2], title: "notes preparing" };
    sendUpdate({ live_pids: [4242], status: "running", phases: rows });
    expect(within(toolbar()).getByText("Running phase 2 · speech preparing")).toBeInTheDocument();
    const cards = document.querySelectorAll(".stepper .step");
    expect(cards[2].querySelector(".step-name")).toHaveTextContent("notes preparing");
    expect(screen.queryByText("frames preparing")).not.toBeInTheDocument();
    await userEvent.click(within(cards[1] as HTMLElement).getByRole("button", { name: "what phase 2 does" }));
    expect(screen.getByText("Phase 2 · speech preparing")).toBeInTheDocument();
    expect(screen.getByText("One model call turns the transcript into paragraphs.")).toBeInTheDocument();
  });

  it("a run holding at its pause reads paused, one still in its call reads pause requested defect", () => {
    // The page said "Pause requested" while the run already held, and the owner took the pause for broken.
    // Now the server says when the run holds, and the page reads "paused" then.
    renderRun();
    sendUpdate({ live_pids: [4242], signal: "pause", is_held: false });
    expect(screen.getByText("pause requested")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("the model call running now finishes first");
    sendUpdate({ live_pids: [4242], signal: "pause", is_held: true });
    expect(screen.getByText("paused")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Paused: the run holds here until you press Continue.");
  });

  it("a live run says what it does now, a request prepared or the model awaited, and since when defect", () => {
    // A run read its frames' screen text for minutes with no model call, the page said only "Running phase 3", and the
    // owner saw the model host idle and took the run for stuck. Now the page says the run's step and since when.
    renderRun();
    sendUpdate({ live_pids: [4242], activity: { text: "preparing the request for frames 4 → 10, no model call yet",
      since: "2026-11-17T11:57:10" } });
    expect(screen.getByText("preparing the request for frames 4 → 10, no model call yet, since 11:57")).toBeInTheDocument();
    sendUpdate({ live_pids: [4242], activity: { text: "waiting for the model's answer for frames-004-010",
      since: "2026-11-17T12:03:00" } });
    expect(screen.getByText("waiting for the model's answer for frames-004-010, since 12:03")).toBeInTheDocument();
  });

  it("live with no phase running yet just says Running", () => {
    renderRun();
    sendUpdate({ live_pids: [1], phases: [] });
    expect(within(toolbar()).getByText("Running")).toBeInTheDocument();
  });

  it("live: sends Pause, shows the waiting signal, and Continue removes it", async () => {
    const calls = installServer({ [`POST ${RUN_BASE}/control`]: (body) =>
      ({ signal: (body as { signal: string }).signal === "continue" ? null : "pause" }) });
    const { onMessage } = renderRun();
    sendUpdate({ live_pids: [4242] });
    await userEvent.click(screen.getByRole("button", { name: "Control" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Pause/ }));
    expect(calls.at(-1)).toEqual({ method: "POST", url: `${RUN_BASE}/control`, body: { signal: "pause" } });
    expect(onMessage).toHaveBeenLastCalledWith("AcmeSync-demo: pause requested; the run acts on it at its next check");

    sendUpdate({ live_pids: [4242], signal: "pause" });
    expect(screen.getByText("pause requested")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Control" }));
    expect(screen.getByRole("menuitem", { name: /Pause/ })).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByRole("menuitem", { name: /Drain/ })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Control" }));
    await userEvent.click(screen.getByRole("button", { name: /Continue/ }));
    expect(calls.at(-1)?.body).toEqual({ signal: "continue" });
    expect(onMessage).toHaveBeenLastCalledWith("AcmeSync-demo: request taken back; the run goes on");
  });

  it("live: Drain and Stop send their signal, and a refused signal is reported", async () => {
    let answer: unknown = { signal: "drain" };
    installServer({ [`POST ${RUN_BASE}/control`]: () => answer });
    const { onMessage } = renderRun();
    sendUpdate({ live_pids: [1], signal: "drain" });
    await userEvent.click(screen.getByRole("button", { name: "Control" }));
    expect(screen.getByRole("menuitem", { name: /Drain/ })).toHaveAttribute("aria-disabled", "true");
    await userEvent.click(screen.getByRole("menuitem", { name: /Stop/ }));
    expect(onMessage).toHaveBeenLastCalledWith("AcmeSync-demo: drain requested; the run acts on it at its next check");
    answer = answerJson({ detail: "no run folder" }, 404);
    await userEvent.click(screen.getByRole("button", { name: "Control" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Stop/ }));
    expect(onMessage).toHaveBeenLastCalledWith("AcmeSync-demo: stop was not sent: 404: no run folder", true);
  });

  it("live: a Stop waiting for the run is said in words, and its button takes it back defect", async () => {
    // The owner pressed Stop and saw only a small "stop requested" pill among the header's and a Continue button that
    // did not say what it continues. A line now says what the stop will do, and the button reads "Cancel the stop"
    const calls = installServer({ [`POST ${RUN_BASE}/control`]: () => ({ signal: null }) });
    renderRun();
    sendUpdate({ live_pids: [4242], signal: "stop" });
    expect(screen.getByRole("status")).toHaveTextContent(
      "Stop requested: the run stops at its next check, before its next model call. Nothing written is lost; a resume goes on from there.");
    await userEvent.click(screen.getByRole("button", { name: "Cancel the stop" }));
    expect(calls.at(-1)?.body).toEqual({ signal: "continue" });
  });

  it("live from this page: Interrupt now interrupts its job", async () => {
    let answer: unknown = makeJob({ job_id: "job-7", status: "running" });
    const calls = installServer({ "POST /api/jobs/job-7/interrupt": () => answer });
    const { onMessage, onChanged } = renderRun({ jobs: [makeJob({ job_id: "job-7", status: "running" })] });
    sendUpdate({ live_pids: [1] });
    await userEvent.click(screen.getByRole("button", { name: "Control" }));
    const item = screen.getByRole("menuitem", { name: /Interrupt now/ });
    expect(item).toHaveTextContent("as Ctrl-C would, without waiting for a check");
    await userEvent.click(item);
    expect(calls.at(-1)).toEqual({ method: "POST", url: "/api/jobs/job-7/interrupt", body: {} });
    expect(onMessage).toHaveBeenLastCalledWith("Job job-7: interrupted");
    expect(onChanged).toHaveBeenCalled();
    answer = answerJson({ detail: "unknown job job-7" }, 404);
    await userEvent.click(screen.getByRole("button", { name: "Control" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Interrupt now/ }));
    expect(onMessage).toHaveBeenLastCalledWith("AcmeSync-demo: the run was not interrupted: 404: unknown job job-7", true);
  });

  it("waiting in the queue: names the job and offers to take it off the queue, and its choice for a failure", () => {
    renderRun({ jobs: [makeJob({ job_id: "job-3" }), makeJob({ job_id: "job-4" }, { output_root: "/elsewhere" })] });
    sendUpdate({});
    expect(within(toolbar()).getByText("Queued as job job-3")).toBeInTheDocument();
    expect(within(toolbar()).getAllByRole("button").map((button) => button.textContent?.trim()))
      .toEqual(["Take off the queue", "On failure"]);
  });

  it("blocked phase: offers Unblock phase N, which queues a resume, and no Resume", async () => {
    const calls = installServer({ "POST /api/jobs": [makeJob({ job_id: "job-5" })] });
    const { onMessage, onChanged } = renderRun();
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "complete", "blocked", "not run"]) });
    expect(within(toolbar()).queryByText(/Resume from/)).not.toBeInTheDocument();
    await userEvent.click(within(toolbar()).getByRole("button", { name: "Unblock phase 4 · document writing…" }));
    expect(lastJobRequest(calls)).toBeUndefined();
    await userEvent.click(await screen.findByRole("button", { name: "Run it now" }));
    expect(lastJobRequest(calls)).toEqual({ items: [{ video: "/work/inbox/AcmeSync-demo.mp4", output: "/work/knowledge/AcmeSync-demo" }],
      only_phase: null, from_phase: null, to_phase: null, worker_indexes: [], is_resuming: true, is_dry_run: false, profile: "", tags: [],
      ingesting_profile: "", is_run_as_audio: false });
    expect(onMessage).toHaveBeenLastCalledWith("Queued an unblock of AcmeSync-demo (job job-5)");
    expect(onChanged).toHaveBeenCalled();
  });

  it("an unblock waits for the provider set to be chosen, and queues on the one chosen defect", async () => {
    // Defect: pressing Unblock queued the run at once, so the owner could not choose to run it or on which provider set.
    // Fix: Unblock opens a window with the provider sets; only its button queues, on the set chosen there.
    const calls = installServer({ "POST /api/jobs": [makeJob({ job_id: "job-5" })], "GET /api/provider-sets": PROVIDER_SETS });
    renderRun();
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "complete", "blocked", "not run"]) });
    await userEvent.click(within(toolbar()).getByRole("button", { name: "Unblock phase 4 · document writing…" }));
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Provider set" }), "gpu-box");
    expect(screen.getByRole("option", { name: "default · main machine (default)" })).toBeInTheDocument();
    expect(lastJobRequest(calls)).toBeUndefined();
    await userEvent.click(screen.getByRole("button", { name: "Run it now" }));
    expect(lastJobRequest(calls)).toMatchObject({ is_resuming: true, provider_set: "gpu-box" });
  });

  it("an unblock window closed with Not now queues nothing defect", async () => {
    // Defect: the Unblock button looked like it starts the run, and its window had no clear way to say no.
    // Fix: the button opens a window with "Run it now" and "Not now: leave the run blocked".
    const calls = installServer({ "POST /api/jobs": [makeJob({ job_id: "job-6" })], "GET /api/provider-sets": PROVIDER_SETS });
    renderRun();
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "complete", "blocked", "not run"]) });
    await userEvent.click(within(toolbar()).getByRole("button", { name: "Unblock phase 4 · document writing…" }));
    await userEvent.click(await screen.findByRole("button", { name: "Not now: leave the run blocked" }));
    expect(screen.queryByRole("button", { name: "Run it now" })).not.toBeInTheDocument();
    expect(lastJobRequest(calls)).toBeUndefined();
  });

  it("a run that never chose a provider set shows the default set and its machine defect", async () => {
    // Defect: the run page showed a provider set only when the run had chosen one, so a run on the default set showed none.
    // Fix: every run shows a Provider set pill; a run with no choice names the default set and its machine.
    installServer({ "GET /api/provider-sets": PROVIDER_SETS });
    renderRun();
    sendUpdate({});
    expect(await screen.findByText("Provider set: default · main machine")).toBeInTheDocument();
  });

  it("a set on the machine of its own name says its name once defect", async () => {
    // Defect: the set "gpu-box" on the machine "gpu-box" read "Provider set: gpu-box · gpu-box". Fix: a machine of the set's own name is left out.
    installServer({ "GET /api/provider-sets": PROVIDER_SETS });
    renderRun();
    sendUpdate({ provider_set: "gpu-box" });
    expect(await screen.findByText("Provider set: gpu-box")).toBeInTheDocument();
    expect(screen.queryByText(/gpu-box · gpu-box/)).not.toBeInTheDocument();
  });

  it("a stopped run resumes on the provider set chosen in the Resume menu defect", async () => {
    // Defect: a stopped run that was not finished could resume only on its own provider set; the menu offered profiles.
    // Fix: the Resume arrow's menu lists every provider set, the main machine's default one named with its machine.
    const calls = installServer({ "POST /api/jobs": [makeJob({ job_id: "job-8" })], "GET /api/provider-sets": PROVIDER_SETS });
    renderRun();
    sendUpdate({});
    await userEvent.click(within(toolbar()).getByRole("button", { name: "resume with another provider set or other settings" }));
    expect(await screen.findByRole("menuitem", { name: /provider set: default · main machine/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitem", { name: /provider set: Mac · laptop/ }));
    expect(lastJobRequest(calls)).toMatchObject({ is_resuming: true, provider_set: "Mac" });
  });

  it("failed phase counts as blocked", () => {
    renderRun();
    sendUpdate({ phases: makePhaseRows(["complete", "failed", "not run", "not run", "not run"]) });
    expect(within(toolbar()).getByRole("button", { name: "Unblock phase 2 · frames preparing…" })).toHaveClass("primary");
  });

  it("blocked frames: Ask blocked frames again queues phase 3 for those frames only", async () => {
    const calls = installServer({ "POST /api/jobs": [makeJob({ job_id: "job-6" })] });
    const { onMessage } = renderRun();
    sendUpdate({ blocked_items: ["frame-012", "frame-040", "notes"],
      phases: makePhaseRows(["complete", "complete", "incomplete", "not run", "not run"]) });
    const ask = within(toolbar()).getByRole("button", { name: "Ask blocked frames 12, 40 again" });
    expect(ask).toHaveClass("primary");
    expect(within(toolbar()).getByRole("button", { name: "Resume from 3 · frames processing" })).not.toHaveClass("primary");
    await userEvent.click(ask);
    expect((lastJobRequest(calls) as { worker_indexes: number[] }).worker_indexes).toEqual([12, 40]);
    expect(onMessage).toHaveBeenLastCalledWith("Queued frames 12, 40 again of AcmeSync-demo (job job-6)");
  });

  it("one blocked frame beside a blocked phase is a plain button", () => {
    renderRun();
    sendUpdate({ blocked_items: ["frame-007"], phases: makePhaseRows(["complete", "complete", "blocked", "not run", "not run"]) });
    expect(within(toolbar()).getByRole("button", { name: "Ask blocked frame 7 again" })).not.toHaveClass("primary");
  });

  it("not complete: offers Resume from N, Run and the resets", async () => {
    const calls = installServer({ "POST /api/jobs": [makeJob({ job_id: "job-8" })] });
    const { onMessage } = renderRun();
    sendUpdate({});
    const resume = within(toolbar()).getByRole("button", { name: "Resume from 3 · frames processing" });
    expect(resume).toHaveClass("primary");
    // the main action stands first, the document's buttons after it, and Run and the resets in the ··· menu (the owner)
    const buttons = within(toolbar()).getAllByRole("button");
    expect(buttons[0]).toBe(resume);
    expect(within(toolbar()).queryByRole("button", { name: "Run" })).not.toBeInTheDocument();
    await userEvent.click(within(toolbar()).getByRole("button", { name: "more actions" }));
    expect(screen.getByRole("menuitem", { name: /Run…/ })).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Reset to phase…/ })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(within(toolbar()).queryByText("Every phase complete")).not.toBeInTheDocument();
    await userEvent.click(resume);
    expect((lastJobRequest(calls) as { is_resuming: boolean }).is_resuming).toBe(true);
    expect(onMessage).toHaveBeenLastCalledWith("Queued a resume of AcmeSync-demo (job job-8)");
  });

  it("resumes and re-runs with the settings profile the run was made with", async () => {
    const calls = installServer({ "POST /api/jobs": [makeJob({ job_id: "job-8" })] });
    renderRun({ run: makeRun({ settings_profile: "strict" }) });
    sendUpdate({ settings_profile: "strict" });
    await userEvent.click(within(toolbar()).getByRole("button", { name: "Resume from 3 · frames processing" }));
    expect(lastJobRequest(calls)).toMatchObject({ is_resuming: true, profile: "strict" });
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Run…/ }));
    await userEvent.click(screen.getByRole("button", { name: "Queue this run" }));
    expect(lastJobRequest(calls)).toMatchObject({ profile: "strict" });
  });

  it("resumes with the default settings, and says so, when the run's settings profile no longer exists", async () => {
    const calls = installServer({ "POST /api/jobs": [makeJob({ job_id: "job-8" })], "GET /api/profiles": { profiles: ["strict"] } });
    renderRun({ run: makeRun({ settings_profile: "strict-gone" }) });
    sendUpdate({ settings_profile: "strict-gone" });
    expect(await screen.findByText(/settings profile strict-gone no longer exists/)).toBeInTheDocument();
    expect(screen.getByText("Settings: default")).toBeInTheDocument();
    expect(screen.queryByText("Settings: strict-gone")).not.toBeInTheDocument();
    await userEvent.click(within(toolbar()).getByRole("button", { name: "Resume from 3 · frames processing" }));
    expect(lastJobRequest(calls)).toMatchObject({ is_resuming: true, profile: "" });
  });

  it("shows a finished document writing as the whole phase, never its first stage again", () => {
    renderRun();
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "complete", "complete", "not run"]), document_stages: [
      { name: "table of contents", counts: { accepted: 12 } }, { name: "chapters", counts: { accepted: 10 } },
      { name: "final summary", counts: { accepted: 1 } }] });
    const bar = document.querySelector<HTMLElement>(".card.frames")!;
    expect(bar.textContent).toContain("Document written");
    expect(bar.textContent).toContain("3 stages done · 23 in all");
    expect(bar.textContent).not.toContain("Writing the table of contents");
  });

  it("names an audio run's ingesting profile once phase 2 is done, as the default when nothing chose it", () => {
    renderRun({ run: makeRun({ is_audio: true, is_profile_chosen: false, ingesting_profile: "knowledge" }) });
    sendUpdate({ is_audio: true, is_profile_chosen: false, ingesting_profile: "knowledge",
      phases: makePhaseRows(["complete", "complete", "complete", "not run", "not run"]) });
    expect(screen.getByText("Ingesting profile: knowledge (default)")).toBeInTheDocument();
    expect(screen.queryByText("Ingesting profile: chosen in phase 2")).not.toBeInTheDocument();
  });

  it("shows a phase that worked and stopped without a status as stopped, never as pending, and lets it be reset", async () => {
    renderRun();
    const phases = makePhaseRows(["complete", "complete", "complete", "not run", "not run"]);
    sendUpdate({ phases, timing: { phases: { finalize: 9240 }, total_s: 9400, is_counting: false },
      document_stages: [{ name: "table of contents", counts: { accepted: 12 } }, { name: "chapters", counts: { accepted: 10, waiting: 3 } }] });
    const card = document.querySelectorAll(".stepper .step")[3] as HTMLElement;
    expect(card.querySelector("small")?.textContent).toContain("stopped");
    expect(card.querySelector("small")?.textContent).not.toContain("pending");
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    expect(screen.getByRole("menuitem", { name: /Reset from chapter/ })).toBeEnabled();
  });

  it("calls a run whose record says running but no process runs it stopped, not running", () => {
    renderRun();
    sendUpdate({ status: "running", live_pids: [] });
    const pills = document.querySelector(".head-pills")!;
    expect(within(pills as HTMLElement).queryByText("running")).not.toBeInTheDocument();
    expect(within(pills as HTMLElement).getByText("stopped")).toBeInTheDocument();
  });

  it("complete in every phase: Run again with the chosen phases, and no Resume", async () => {
    const calls = installServer({ "POST /api/jobs": [makeJob({ job_id: "job-9" })] });
    renderRun();
    sendUpdate({ status: "complete", phases: makePhaseRows(["complete", "complete", "complete", "complete", "complete"]) });
    expect(within(toolbar()).queryByText("Every phase complete")).not.toBeInTheDocument();
    expect(within(toolbar()).queryByText(/Resume from/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Run again…/ }));
    await userEvent.click(screen.getByRole("radio", { name: "From" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Phase" }), "4");
    await userEvent.click(screen.getByRole("button", { name: "Queue this run" }));
    expect(lastJobRequest(calls)).toMatchObject({ from_phase: 4, only_phase: null, is_resuming: false });
    expect(screen.queryByRole("button", { name: "Queue this run" })).not.toBeInTheDocument();
  });

  it("a finished run offers no resume, and its actions stand beside its title defect", () => {
    // A done run whose critique collection had not run offered "Resume from 6 · critique collection", and its buttons took a row of their
    // own under the title; a finished run now has no Resume, and its actions sit at the right of the title
    installServer({});
    renderRun();
    sendUpdate({ status: "complete", has_document: true, document_file: "gateway/gateway.md",
      phases: makePhaseRows(["complete", "complete", "complete", "complete", "complete", "not run"]) });
    expect(screen.queryByText(/Resume from/)).not.toBeInTheDocument();
    const actions = document.querySelector<HTMLElement>(".detail-head .head-actions")!;
    expect(within(actions).getByRole("button", { name: /Open document/ })).toBeInTheDocument();
    expect(within(actions).getByRole("button", { name: "more actions" })).toBeInTheDocument();
  });

  it("Open document opens the document full screen, and Download is in its menu defect", async () => {
    // Open document did nothing on a run already showing its document, and Download was a button of its own; Open now
    // shows the document full screen, and the download is the one item of the button's menu
    installServer({
      [`GET ${RUN_BASE}/artifacts`]: [{ key: "document", title: "Document", kind: "markdown",
        items: [{ path: "gateway/gateway.md", name: "gateway.md", size_bytes: 30, is_hidden: false }] }],
      [`GET ${RUN_BASE}/files/gateway/gateway.md`]: answerText("# Gateway\n\nThe refunds wait.\n") });
    renderRun();
    sendUpdate({ status: "complete", has_document: true, document_file: "gateway/gateway.md",
      phases: makePhaseRows(["complete", "complete", "complete", "complete", "complete"]) });
    expect(screen.queryByRole("link", { name: /Download document/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "more ways to get the document" }));
    expect(screen.getByRole("menuitem", { name: /Download document/ })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: /Open document/ }));
    expect(await screen.findByRole("dialog", { name: "gateway/gateway.md" })).toBeInTheDocument();
  });

  it("a recording kept in the run folder: phases 1 and 2 are not offered, Run again queues from 3, Move recording to inbox moves it", async () => {
    const calls = installServer({ [`POST ${RUN_BASE}/recording/to-inbox`]: { exit_code: 0, output: "recording moved back\n" },
      "POST /api/jobs": [makeJob({ job_id: "job-11" })] });
    const { onMessage } = renderRun();
    sendUpdate({ status: "complete", is_recording_kept: true, has_recording: true,
      phases: makePhaseRows(["complete", "complete", "complete", "complete", "complete"]) });
    expect(screen.queryByRole("button", { name: "phase 1 actions" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "phase 2 actions" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "phase 3 actions" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Run again…/ }));
    expect(screen.getByText(/kept in this run folder: Move recording to inbox to run them/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: "From" }));
    expect(within(screen.getByRole("combobox", { name: "Phase" })).getAllByRole("option")
      .map((option) => option.textContent?.split(" ·")[0])).toEqual(["3", "4", "5"]);
    await userEvent.click(screen.getByRole("radio", { name: "Every phase" }));
    await userEvent.click(screen.getByRole("button", { name: "Queue this run" }));
    expect(lastJobRequest(calls)).toMatchObject({ from_phase: 3, only_phase: null, is_resuming: false });
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    expect(screen.queryByRole("menuitem", { name: /Move recording to run folder/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitem", { name: /Move recording to inbox/ }));
    expect(calls.some((call) => call.url === `${RUN_BASE}/recording/to-inbox`)).toBe(true);
    expect(onMessage).toHaveBeenLastCalledWith("recording moved back", false);
  });

  it("a done run whose recording is in the inbox offers Move recording to run folder", async () => {
    const calls = installServer({ [`POST ${RUN_BASE}/recording/to-run`]: { exit_code: 0, output: "recording kept\n" } });
    const { onMessage } = renderRun();
    sendUpdate({ status: "complete", can_keep_recording: true,
      phases: makePhaseRows(["complete", "complete", "complete", "complete", "complete"]) });
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    expect(screen.queryByRole("menuitem", { name: /Move recording to inbox/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitem", { name: /Move recording to run folder/ }));
    expect(calls.some((call) => call.url === `${RUN_BASE}/recording/to-run`)).toBe(true);
    expect(onMessage).toHaveBeenLastCalledWith("recording kept", false);
  });

  it("a move shows that it is working until the page sees the recording in its new place", async () => {
    let finish: (answer: { exit_code: number; output: string }) => void = () => {};
    installServer({ [`POST ${RUN_BASE}/recording/to-inbox`]: () => new Promise((resolve) => { finish = resolve; }) });
    renderRun();
    sendUpdate({ status: "complete", is_recording_kept: true, has_recording: true,
      phases: makePhaseRows(["complete", "complete", "complete", "complete", "complete"]) });
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Move recording to inbox/ }));
    const busy = within(toolbar()).getByText(/Moving recording to inbox/);
    expect(busy.querySelector(".spinner")).not.toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    expect(screen.queryByRole("menuitem", { name: /Move recording/ })).not.toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await act(async () => finish({ exit_code: 0, output: "moved\n" }));
    expect(within(toolbar()).getByText(/Moving recording to inbox/)).toBeInTheDocument();
    sendUpdate({ status: "complete", is_recording_kept: false, can_keep_recording: true, has_recording: true,
      phases: makePhaseRows(["complete", "complete", "complete", "complete", "complete"]) });
    expect(within(toolbar()).queryByText(/Moving recording/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    expect(screen.getAllByRole("menuitem", { name: /recording/ }).length).toBeGreaterThan(0);
  });

  it("Move recording to trash moves it to the inbox trash and says how to bring it back", async () => {
    const calls = installServer({ [`POST ${RUN_BASE}/recording/delete`]: { moved_as: "AcmeSync-demo.mp4" } });
    const { onMessage } = renderRun();
    sendUpdate({ status: "complete", has_recording: true,
      phases: makePhaseRows(["complete", "complete", "complete", "complete", "complete"]) });
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    expect(screen.queryByRole("menuitem", { name: /Upload recording/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitem", { name: /Move recording to trash/ }));
    expect(calls.some((call) => call.url === `${RUN_BASE}/recording/delete`)).toBe(true);
    expect(onMessage).toHaveBeenLastCalledWith(
      "AcmeSync-demo: recording moved to the inbox trash as AcmeSync-demo.mp4; Restore there brings it back", false);
    expect(within(toolbar()).getByText(/Moving recording to trash/)).toBeInTheDocument();
  });

  it("a PDF run's menu names the PDF, never a recording defect", async () => {
    // A PDF run's "PDF" menu offered "Move recording to inbox" and "Move recording to trash", though a PDF is no recording.
    // Every line about it now names it "PDF": the menu, its working line and the reason phases 1 and 2 are locked.
    installServer({ [`POST ${RUN_BASE}/recording/delete`]: () => new Promise(() => {}) });
    renderRun();
    sendUpdate({ status: "complete", pdf_mode: "text-only", is_recording_kept: true, has_recording: true,
      phases: makePhaseRows(["complete", "complete", "complete", "complete", "complete"]) });
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    expect(screen.getByRole("menuitem", { name: /Move PDF to inbox/ })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /recording/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitem", { name: /Move PDF to trash/ }));
    expect(within(toolbar()).getByText(/Moving PDF to trash/)).toBeInTheDocument();
  });

  it("Upload recording, offered only when there is none, sends the chosen file", async () => {
    const calls = installServer({ [`POST ${RUN_BASE}/recording/upload`]: { saved_as: "/work/inbox/AcmeSync-demo.mp4" } });
    const { onMessage } = renderRun();
    sendUpdate({ has_recording: false, phases: makePhaseRows(["complete", "complete", "not run", "not run", "not run"]) });
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    expect(screen.queryByRole("menuitem", { name: /Move recording to trash/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitem", { name: /Upload recording/ }));
    await userEvent.upload(screen.getByLabelText("recording file"), new File(["<video bytes>"], "phone-copy.mp4", { type: "video/mp4" }));
    expect(calls.some((call) => call.url === `${RUN_BASE}/recording/upload` && call.method === "POST")).toBe(true);
    expect(onMessage).toHaveBeenLastCalledWith("AcmeSync-demo: recording saved as /work/inbox/AcmeSync-demo.mp4", false);
  });

  it("a recording action that fails says why and offers the menu again", async () => {
    installServer({ [`POST ${RUN_BASE}/recording/delete`]: answerJson({ detail: "a run is writing into this folder" }, 409) });
    const { onMessage } = renderRun();
    sendUpdate({ has_recording: true });
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Move recording to trash/ }));
    expect(onMessage).toHaveBeenLastCalledWith("AcmeSync-demo: moving recording to trash failed: 409: a run is writing into this folder", true);
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    expect(screen.getAllByRole("menuitem", { name: /recording/ }).length).toBeGreaterThan(0);
  });

  it("a recording found nowhere: no resume or action for phases 1 and 2, and no move button", () => {
    renderRun();
    sendUpdate({ is_recording_kept: false, has_recording: false,
      phases: makePhaseRows(["complete", "not run", "not run", "not run", "not run"]) });
    expect(within(toolbar()).queryByRole("button", { name: /Resume from 2/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "phase 2 actions" })).not.toBeInTheDocument();
  });

  it("refuses to queue a folder whose run record names no video, and reports a refused queue", async () => {
    installServer({ "POST /api/jobs": answerJson({ detail: "queue file locked" }, 500) });
    const { onMessage } = renderRun({ run: makeRun({ video: "" }) });
    sendUpdate({ video: "" });
    await userEvent.click(within(toolbar()).getByRole("button", { name: /Resume from/ }));
    expect(onMessage).toHaveBeenLastCalledWith("AcmeSync-demo has no run record naming its video; process it from the inbox.", true);
    sendUpdate({ video: "/work/inbox/AcmeSync-demo.mp4", run_id: "" });
    await userEvent.click(within(toolbar()).getByRole("button", { name: /Resume from/ }));
    expect(onMessage).toHaveBeenLastCalledWith("AcmeSync-demo: a resume was not queued: 500: queue file locked", true);
  });
});

describe("RunDetail phases", () => {
  it("each phase's menu runs from it, runs it alone, or opens the reset to the phase before", async () => {
    const calls = installServer({ "POST /api/jobs": [makeJob()] });
    const { onMessage } = renderRun();
    sendUpdate({});
    const steps = document.querySelectorAll(".stepper .step");
    expect([...steps].map((step) => step.querySelector(".step-name")?.textContent)).toEqual(PHASES.map((phase) => phase.name === "finalize" ? "document writing" : phase.name.replace(/_/g, " ")));
    expect(steps[2]).toHaveClass("not-run");
    await userEvent.click(screen.getByRole("button", { name: "phase 2 actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Run from 2/ }));
    expect(lastJobRequest(calls)).toMatchObject({ from_phase: 2, only_phase: null });
    expect(onMessage).toHaveBeenLastCalledWith("Queued phases 2 onward of AcmeSync-demo (job job-1)");
    await userEvent.click(screen.getByRole("button", { name: "phase 2 actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Run only 2/ }));
    expect(lastJobRequest(calls)).toMatchObject({ from_phase: null, only_phase: 2 });
    await userEvent.click(screen.getByRole("button", { name: "phase 2 actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Reset to 1/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Reset to phase" })).toBeInTheDocument();
    const keep = within(dialog).getByRole("combobox", { name: "Keep phases up to" });
    expect(keep).toHaveValue("1");
    expect([...keep.querySelectorAll("option")].map((option) => option.textContent)).toEqual([
      "0 · none, every phase removed", "1 · transcribe", "2 · frames preparing", "3 · frames processing", "4 · document writing",
      "5 · review"]);
  });

  it("the chapter a live run works on shows as running, never as its old block", () => {
    renderRun();
    sendUpdate({ status: "running", live_pids: [41], phases: makePhaseRows(["complete", "complete", "complete", "running", "not run"]),
      document_stages: [{ name: "table of contents", counts: { accepted: 9 } }, { name: "chapters", counts: { accepted: 6, running: 1 } },
        { name: "final summary", counts: { waiting: 1 } }], is_document_total_known: true });
    const legend = document.querySelector(".card.frames .legend")!;
    expect(legend.textContent).toContain("running 1");
    expect(legend.textContent).not.toContain("blocked");
    expect(document.querySelector(".card.frames .seg.running")).not.toBeNull();
  });

  it("lists every step that blocked with its counts and each block's reason, and no card when none blocked", () => {
    renderRun();
    sendUpdate({ step_outcomes: [
      { phase: 4, step: "chapter-02-text", blocked: 0, accepted: 1, reset: 0, blocks: [] },
      { phase: 4, step: "chapter-03-text-1", blocked: 2, accepted: 1, reset: 1, blocks: [
        { reason: "1 answers rejected: CODE_A, CODE_A", summary: "rejected: CODE_A ×2", at: "2026-10-01T09:15:00+02:00" },
        { reason: "the host failed", summary: "the host failed", at: "" }] }] });
    const card = document.querySelector(".card.step-blocks")!;
    expect(card.querySelector("summary")!.textContent).toContain("2 over 1 step");
    expect(card.textContent).toContain("phase 4 · chapter-03-text-1");
    expect(card.textContent).toContain("blocked 2 · accepted 1 · reset 1");
    expect(card.textContent).not.toContain("chapter-02-text");
    // the reason is written under the block, not left in a tooltip; a reason that is the summary is not written twice
    const firstBlock = card.querySelectorAll(".block-list li")[0];
    expect(firstBlock.textContent).toContain("2026-10-01 09:15");
    expect(firstBlock.textContent).toContain("1 answers rejected: CODE_A, CODE_A");
    expect(card.querySelectorAll(".block-list li")[1].textContent).toBe("no time the host failed");
    expect(card.textContent).toContain("no time");
    sendUpdate({ step_outcomes: [{ phase: 4, step: "chapter-02-text", blocked: 0, accepted: 1, reset: 0, blocks: [] }] });
    expect(document.querySelector(".card.step-blocks")).toBeNull();
  });

  it("the ? beside a phase says what it does, reads and writes, and whether it calls the model", async () => {
    renderRun();
    sendUpdate({});
    await userEvent.click(screen.getByTitle("what phase 3 does"));
    const help = document.querySelector<HTMLElement>(".phase-help")!;
    expect(within(help).getByText("Phase 3 · frames processing")).toBeInTheDocument();
    expect(within(help).getByText("A vision model reads the frames in batches.")).toBeInTheDocument();
    expect(within(help).getByText("02_frames.json")).toBeInTheDocument();
    expect(within(help).getByText("yes, it calls the model")).toBeInTheDocument();
    await userEvent.click(screen.getByTitle("what phase 1 does"));
    expect(screen.getByText("no, plain code")).toBeInTheDocument();
    expect(screen.queryByTitle("what phase 5 does")).not.toBeInTheDocument();
  });

  it("shows the bar of the phase at work: frames for phase 3, steps for phase 4, none once every phase is complete", () => {
    renderRun();
    const frames = { pending: 0, analyzed: 0, written: 96, skipped: 0, blocked: 0 };
    const stagesAt = (contents: Record<string, number>, chapters: Record<string, number>, final: Record<string, number>) => [
      { name: "table of contents", counts: contents }, { name: "chapters", counts: chapters }, { name: "final summary", counts: final }];
    sendUpdate({ live_pids: [1], frames, phases: makePhaseRows(["complete", "complete", "running", "not run", "not run"]) });
    expect(screen.getByText("Frames")).toBeInTheDocument();
    sendUpdate({ live_pids: [1], frames, document_stages: stagesAt({ accepted: 1, waiting: 1 }, {}, { waiting: 1 }),
      is_document_total_known: false, phases: makePhaseRows(["complete", "complete", "complete", "running", "not run"]) });
    expect(screen.queryByText("Frames")).toBeNull();
    expect(screen.getByText("Writing the table of contents")).toBeInTheDocument();
    expect(screen.getByText("the chapters' headings are counted once the agenda is accepted")).toBeInTheDocument();
    sendUpdate({ live_pids: [1], frames, document_stages: stagesAt({ accepted: 9 }, { accepted: 6, blocked: 2, waiting: 1 }, { waiting: 1 }),
      is_document_total_known: true, phases: makePhaseRows(["complete", "complete", "complete", "running", "not run"]) });
    expect(screen.getByText("Writing the chapters")).toBeInTheDocument();
    const legend = [...document.querySelectorAll(".legend > span")].map((entry) => entry.textContent?.trim());
    expect(legend).toEqual(["accepted 6", "blocked 2", "waiting 1"]);
    expect(within(document.querySelector(".frames-head")!).getByText("stage 2 of 3 · 9 in all")).toBeInTheDocument();
    expect(screen.queryByText(/counted once the agenda/)).toBeNull();
    sendUpdate({ live_pids: [1], frames, document_stages: stagesAt({ accepted: 9 }, { accepted: 9 }, { waiting: 1 }),
      is_document_total_known: true, phases: makePhaseRows(["complete", "complete", "complete", "running", "not run"]) });
    expect(screen.getByText("Writing the final summary")).toBeInTheDocument();
    sendUpdate({ frames, document_stages: stagesAt({ accepted: 9 }, { accepted: 9 }, { accepted: 1 }), is_document_total_known: true,
      phases: makePhaseRows(["complete", "complete", "complete", "complete", "complete"]) });
    expect(document.querySelector(".frame-bar")).toBeNull();
  });

  it("opens phase 4's card on every step of the document writing, and notes a phase that had nothing to do", async () => {
    installServer({ [`GET ${RUN_BASE}/document-steps`]: [
      { slug: "parts", stage: "table of contents", step: "agenda", status: "accepted", calls: 1, seconds: 60,
        prompt_tokens: 1000, output_tokens: 100, rejections: [] }] });
    renderRun();
    const phases = makePhaseRows(["complete", "complete", "complete", "running", "not run"]);
    phases[2] = { ...phases[2], note: "nothing to prepare" };
    sendUpdate({ live_pids: [1], phases, is_document_total_known: true, document_stages: [
      { name: "table of contents", counts: { accepted: 1 } }, { name: "chapters", counts: { waiting: 2 } },
      { name: "final summary", counts: { waiting: 1 } }] });
    expect(screen.getByText(/nothing to prepare/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "show step details" }));
    expect(await screen.findByText("agenda")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "hide step details" }));
    expect(screen.queryByText("agenda")).toBeNull();
  });

  it("opens phase 5's card on every step of the review with its rejections, as phase 4's", async () => {
    installServer({ [`GET ${RUN_BASE}/review-steps`]: [
      { slug: "gaps", stage: "review", step: "gaps", status: "accepted", calls: 2, seconds: 90, prompt_tokens: 1000,
        output_tokens: 100, rejections: [{ findings: [{ code: "GAP_UNSOURCED", text: "a gap names no line" }] }] }] });
    renderRun();
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "complete", "complete", "blocked"]) });
    await userEvent.click(screen.getByRole("button", { name: "show step details" }));
    expect(await screen.findByText("GAP_UNSOURCED")).toBeInTheDocument();
  });

  it("orders the frames bar written, skipped, analyzed, blocked, pending, then any other status", () => {
    renderRun();
    sendUpdate({ live_pids: [1], frames: { pending: 5, retried: 1, blocked: 1, analyzed: 0, written: 3, skipped: 2 },
      phases: makePhaseRows(["complete", "complete", "running", "not run", "not run"]) });
    const legend = [...document.querySelectorAll(".legend > span")].map((entry) => entry.textContent?.trim());
    expect(legend).toEqual(["written 3", "skipped 2", "blocked 1", "pending 5", "retried 1"]);
    const segments = [...document.querySelectorAll(".frame-bar .seg")].map((segment) => segment.getAttribute("title"));
    expect(segments).toEqual(["written 3", "skipped 2", "blocked 1", "pending 5", "retried 1"]);
    expect(document.querySelector(".frame-bar")).toHaveClass("live");
    expect(within(document.querySelector(".frames-head")!).getByText("12 in all")).toBeInTheDocument();
  });

  it("shows the Recording tab only while the run's recording exists, and a frame's Play from opens it there", async () => {
    installServer({ [`GET ${RUN_BASE}/artifacts`]: [{ key: "frames", title: "Frames", kind: "images",
      items: [{ path: "frames/frame-001.jpg", name: "frame-001.jpg", size_bytes: 1, time_s: 127 }] }] });
    renderRun();
    sendUpdate({ has_recording: false });
    expect(screen.queryByRole("tab", { name: "Recording" })).not.toBeInTheDocument();
    sendUpdate({ has_recording: true, recording_parts: [{ name: "AcmeSync-demo.mp4", size_bytes: 9, start_s: 0, duration_s: 900 }] });
    await userEvent.click(screen.getByRole("tab", { name: "Artifacts" }));
    await userEvent.click(await screen.findByRole("button", { name: /frame-001/ }));
    await userEvent.click(screen.getByRole("button", { name: "Play from 2:07" }));

    expect(screen.getByRole("tab", { name: "Recording" })).toHaveAttribute("aria-selected", "true");
    const player = document.querySelector<HTMLVideoElement>(".recording video")!;
    expect(player).toHaveAttribute("src", `${RUN_BASE}/recording`);
    fireEvent(player, new Event("loadedmetadata"));
    expect(player.currentTime).toBe(127);
    await userEvent.click(screen.getByRole("tab", { name: "Progress" }));
    expect(document.querySelector(".recording")).toBeNull();
  });

  it("shows the meeting's topic under the run's name once phase 4 has written it", () => {
    renderRun();
    sendUpdate({});
    expect(document.querySelector(".detail-topic")).toBeNull();
    sendUpdate({ topic: "Gateway token exchange" });
    expect(screen.getByText("Gateway token exchange")).toHaveClass("detail-topic");
  });

  it("shows the run time as text at the header's top right, counting while the run is live, and each timed phase's time", () => {
    renderRun();
    sendUpdate({});
    expect(screen.queryByText("Total processing time")).not.toBeInTheDocument();
    sendUpdate({ live_pids: [1], timing: { phases: { transcribe: 45, frames_preparing: 192 }, total_s: 3840, is_counting: true } });
    const runTime = screen.getByText("Total processing time").closest(".run-time") as HTMLElement;
    expect(runTime).toHaveClass("counting");
    expect(runTime).toHaveTextContent("Total processing time1h 04m");
    expect(runTime).not.toHaveClass("pill");
    expect(runTime.closest(".head-right")).not.toBeNull();
    const times = [...document.querySelectorAll(".stepper .step small")].map((status) => status.textContent);
    expect(times).toEqual(["complete · 45s", "complete · 3m 12s", "pending", "pending", "pending", "pending"]);
    sendUpdate({ timing: { phases: {}, total_s: 3840, is_counting: false } });
    expect(document.querySelector(".run-time")).not.toHaveClass("counting");
  });

  it("shows the time each frame took on average under the total processing time, labelled in words defect", () => {
    // The corner read "Run time 52h 28m" over "21m 25s / fs", a symbol and an abbreviation the owner had to decode.
    // Now two labelled rows: "Total processing time" and "Each frame ... on average", labels left and values right
    renderRun();
    sendUpdate({ live_pids: [1], frames: { pending: 2, accepted: 1, blocked: 1 },
                 timing: { phases: { transcribe: 60, frames_processing: 1000 }, total_s: 1200, is_counting: true } });
    const values = () => [...document.querySelectorAll(".run-time .run-time-value")].map((value) => value.textContent);
    expect([...document.querySelectorAll(".run-time .run-time-label")].map((label) => label.textContent))
      .toEqual(["Total processing time", "Each frame"]);
    expect(values()).toEqual(["20m 00s", "5m 00s on average"]);
    expect(document.querySelector(".run-time")?.textContent).not.toContain("/ fs");
    sendUpdate({ live_pids: [1], frames: { pending: 2, accepted: 1, blocked: 1 },
                 timing: { phases: { transcribe: 60, frames_processing: 1000 }, total_s: 2400, is_counting: true } });
    expect(values()).toEqual(["40m 00s", "10m 00s on average"]);
  });

  it("leaves the each-frame row out while phase 2 has kept no frame yet defect", () => {
    // The row said "once phase 2 has kept its frames" in the corner, a line the owner found too long. Now the row
    // is left out until there are frames to divide by
    renderRun();
    sendUpdate({ live_pids: [1], frames: { pending: 0, accepted: 0, blocked: 0 },
                 timing: { phases: { transcribe: 850 }, total_s: 2016, is_counting: true } });
    expect(screen.getByText("Total processing time")).toBeInTheDocument();
    expect(screen.queryByText("Each frame")).not.toBeInTheDocument();
  });

  it("shows no frames card while no frame has a status, and the log under its file name", () => {
    renderRun();
    sendUpdate({}, "phase 1 started\n", 16);
    expect(document.querySelector(".frames")).not.toBeInTheDocument();
    expect(screen.getByText("20261001-120000/12-00.log")).toBeInTheDocument();
    expect(screen.getByText("phase 1 started")).toBeInTheDocument();
  });

  it("switches to Artifacts and back to Progress", async () => {
    installServer({ [`GET ${RUN_BASE}/artifacts`]: [] });
    renderRun();
    sendUpdate({});
    await userEvent.click(screen.getByRole("tab", { name: "Artifacts" }));
    expect(await screen.findByText("This run has produced nothing to look at yet.")).toBeInTheDocument();
    expect(document.querySelector(".stepper")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Progress" }));
    expect(document.querySelector(".stepper")).toBeInTheDocument();
  });
});

describe("RunDetail reset", () => {
  it("lists what a reset removes as it opens and as the phase changes, and Reset runs it", async () => {
    const calls = installServer({ [`POST ${RUN_BASE}/reset`]: (body) => {
      const { is_confirmed, number } = body as { is_confirmed: boolean; number: number };
      return is_confirmed ? { exit_code: 0, output: "reset-to 2: removed from /work/AcmeSync-demo:\n  03_notes.md\n" } : { exit_code: 0, output: `would remove after ${number}` };
    } });
    const { onMessage, onChanged } = renderRun();
    sendUpdate({});
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Reset to phase/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/0 removes every phase/)).toBeInTheDocument();
    // it opens keeping what is complete: phases 1 and 2, phase 3 being the first not complete
    expect(await within(dialog).findByText("would remove after 2")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "List what it removes" })).not.toBeInTheDocument();
    await userEvent.selectOptions(within(dialog).getByRole("combobox", { name: "Keep phases up to" }), "1");
    expect(await within(dialog).findByText("would remove after 1")).toBeInTheDocument();
    expect(calls.at(-1)?.body).toEqual({ kind: "phase", number: 1, is_confirmed: false });
    await userEvent.click(within(dialog).getByRole("button", { name: "Reset" }));
    expect(calls.at(-1)?.body).toEqual({ kind: "phase", number: 1, is_confirmed: true });
    expect(onMessage).toHaveBeenLastCalledWith("AcmeSync-demo: reset to phase 1, 1 removed", false);
    expect(onChanged).toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows why the listing failed instead of listing for ever", async () => {
    installServer({ [`POST ${RUN_BASE}/reset`]: answerJson({ detail: "the run folder is locked" }, 409) });
    const { onMessage } = renderRun();
    sendUpdate({});
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Reset to phase/ }));
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText("409: the run folder is locked")).toBeInTheDocument();
    expect(within(dialog).queryByText(/Listing what this reset removes/)).not.toBeInTheDocument();
    expect(onMessage).not.toHaveBeenCalledWith(expect.stringContaining("locked"), expect.anything());
  });

  it("shows Resetting and takes no second click while a reset runs", async () => {
    let finish: (answer: { exit_code: number; output: string }) => void = () => {};
    const calls = installServer({ [`POST ${RUN_BASE}/reset`]: (body) => (body as { is_confirmed: boolean }).is_confirmed
      ? new Promise<{ exit_code: number; output: string }>((resolve) => { finish = resolve; }) : { exit_code: 0, output: "would remove" } });
    renderRun();
    sendUpdate({});
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Reset to phase/ }));
    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reset" }));
    const busy = within(dialog).getByRole("button", { name: /Resetting/ });
    expect(busy).toBeDisabled();
    expect(busy.querySelector(".spinner")).not.toBeNull();
    await userEvent.click(busy);
    expect(calls.filter((call) => (call.body as { is_confirmed?: boolean })?.is_confirmed)).toHaveLength(1);
    await act(async () => finish({ exit_code: 0, output: "reset-to 4: removed from /work:\n  a\n  b\n" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("offers the run's chapters by number and title in Reset from chapter, and to keep their headings", async () => {
    const calls = installServer({ [`POST ${RUN_BASE}/reset`]: { exit_code: 0, output: "would remove" } });
    renderRun();
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "complete", "blocked", "not run"]),
      chapters: [{ number: 1, title: "Login flow" }, { number: 2, title: "Token refresh" }] });
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Reset from chapter/ }));
    const chapter = within(screen.getByRole("dialog")).getByRole("combobox", { name: "Chapter" });
    expect([...chapter.querySelectorAll("option")].map((option) => option.textContent)).toEqual(["1 · Login flow", "2 · Token refresh"]);
    // the headings can be kept, so only the chapters' texts are written again
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("checkbox", { name: /Keep the headings/ }));
    expect(calls.at(-1)?.body).toMatchObject({ kind: "chapter", is_text_only: true, is_confirmed: false });
  });

  it("shows a refused listing and still offers Reset; Cancel closes", async () => {
    installServer({ [`POST ${RUN_BASE}/reset`]: { exit_code: 2, output: "a run process writes into the folder" } });
    renderRun();
    // a chapter can be taken back once phase 4 has started
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "complete", "blocked", "not run"]) });
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Reset from chapter/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Reset from chapter" })).toBeInTheDocument();
    expect(within(dialog).getByText(/Removes the chosen chapter and every later chapter/)).toBeInTheDocument();
    expect(within(dialog).getByRole("spinbutton", { name: "Chapter" })).toHaveValue(1);
    expect(await within(dialog).findByText("a run process writes into the folder")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Reset" })).toBeEnabled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("reports a failed reset with its exit code, and a request the server refused", async () => {
    let answer: unknown = { exit_code: 0, output: "nothing to remove" };
    installServer({ [`POST ${RUN_BASE}/reset`]: (body) => (body as { is_confirmed: boolean }).is_confirmed
      ? { exit_code: 3, output: "  " } : answer });
    const { onMessage } = renderRun();
    sendUpdate({});
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Reset to phase/ }));
    const dialog = screen.getByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reset" }));
    expect(onMessage).toHaveBeenLastCalledWith("exit code 3", true);

    answer = answerJson({ detail: "unknown run folder" }, 404);
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Reset to phase/ }));
    await within(screen.getByRole("dialog")).findByRole("button", { name: "Reset" });
    // a listing that fails says why in the dialog, in place of the listing
    expect(await within(screen.getByRole("dialog")).findByText("404: unknown run folder")).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("RunDetail critique card", () => {
  it("shows the anti-patterns the run's own batches reported, and opens the rule set", () => {
    renderRun();
    sendUpdate({ critique: { files: 3, rules: 5, anti_patterns: 2, top: "An arrow is kept without checking its head",
      top_batches: 2, top_frames: 4, rule_set: "06_critique-collection/critique-collection.md" } });
    const card = document.querySelector<HTMLElement>(".run-critique")!;
    expect(within(card).getByText("Critique")).toBeInTheDocument();
    expect(within(card).getByText("· 2 anti-patterns from 5 rules over 3 batches")).toBeInTheDocument();
    expect(within(card).getByText("An arrow is kept without checking its head")).toBeInTheDocument();
    expect(card.textContent).toContain("Seen in 2 batches, 4 frames");
    expect(within(card).getByRole("button", { name: "Open the rule set" })).toBeInTheDocument();
  });

  it("says nothing at all when no batch reported a rule", () => {
    renderRun();
    sendUpdate({ critique: { files: 1, rules: 0, anti_patterns: 0, top: "", top_batches: 0, top_frames: 0, rule_set: "" } });
    expect(document.querySelector(".run-critique")).toBeNull();
  });
});

describe("RunDetail worker feedback and the frames average", () => {
  it("says a worker reported a problem, and opens the file it is in", async () => {
    renderRun();
    sendUpdate({ worker_feedback: { entries: 2, reports: 3, open: 2, newest_kind: "unclear_instruction",
      newest_summary: "the draft length rule can be read two ways", newest_answer: "", file: "worker-feedback.md" } });
    const card = document.querySelector<HTMLDetailsElement>(".worker-feedback")!;
    // the newest report shows open: its kind and summary are read without a click
    expect(card.open).toBe(true);
    expect(within(card).getByText("A worker reported a problem")).toBeInTheDocument();
    expect(within(card).getByText("· 2 problems, 3 reports in all")).toBeInTheDocument();
    expect(within(card).getByText("unclear_instruction")).toBeInTheDocument();
    expect(card.textContent).toContain("the draft length rule can be read two ways");
    await userEvent.click(within(card).getByRole("button", { name: "Read worker-feedback.md" }));
    expect(screen.getByRole("tab", { name: "Artifacts", selected: true })).toBeInTheDocument();
  });

  it("says a report is answered, and what was decided, once the watcher answered it defect", async () => {
    // Defect: a report judged and fixed hours before stood on the page as "A worker reported a problem", with nothing
    // saying it was read. Fix: the card reads the answers file: answered, it says so and shows the decision.
    renderRun();
    sendUpdate({ worker_feedback: { entries: 1, reports: 2, open: 1, newest_kind: "wrong_rejection",
      newest_summary: "the repeat check refuses a fixed line", newest_answer: "", file: "worker-feedback.md" } });
    let card = document.querySelector<HTMLElement>(".worker-feedback")!;
    expect(within(card).getByText("A worker reported a problem")).toBeInTheDocument();
    expect(within(card).getByText("Not answered yet")).toBeInTheDocument();
    sendUpdate({ worker_feedback: { entries: 1, reports: 2, open: 0, newest_kind: "wrong_rejection",
      newest_summary: "the repeat check refuses a fixed line", newest_answer: "right: the check is fixed",
      file: "worker-feedback.md" } });
    card = document.querySelector<HTMLElement>(".worker-feedback")!;
    expect(card.classList.contains("answered")).toBe(true);
    expect(within(card).getByText("Worker reports answered")).toBeInTheDocument();
    expect(card.textContent).toContain("Answered: right: the check is fixed");
  });

  it("OK puts the card away until a worker reports one more", async () => {
    renderRun();
    const reported = (reports: number) => sendUpdate({ worker_feedback: { entries: 1, reports, open: 1,
      newest_kind: "unclear_instruction", newest_summary: "the rule can be read two ways", newest_answer: "",
      file: "worker-feedback.md" } });
    reported(1);
    await userEvent.click(within(document.querySelector<HTMLElement>(".worker-feedback")!)
      .getByRole("button", { name: "hide this report until a worker reports another" }));
    expect(document.querySelector(".worker-feedback")).toBeNull();
    reported(1);
    expect(document.querySelector(".worker-feedback")).toBeNull();
    reported(2);
    expect(document.querySelector(".worker-feedback")).not.toBeNull();
  });

  it("keeps nothing in the view cookie for a run with no report put away, and one entry for one that has", async () => {
    renderRun();
    sendUpdate({});
    expect(Object.keys(readViewState()).filter((key) => key.startsWith("feedbackSeen"))).toEqual([]);
    sendUpdate({ worker_feedback: { entries: 1, reports: 2, open: 1, newest_kind: "unclear_instruction",
      newest_summary: "two readings", newest_answer: "", file: "worker-feedback.md" } });
    await userEvent.click(within(document.querySelector<HTMLElement>(".worker-feedback")!)
      .getByRole("button", { name: "hide this report until a worker reports another" }));
    expect(readViewState().feedbackSeen).toEqual({ "knowledge/AcmeSync-demo": 2 });
  });

  it("says nothing when no worker reported anything", () => {
    renderRun();
    sendUpdate({});
    expect(document.querySelector(".worker-feedback")).toBeNull();
  });

  it("an audio recording's phase 3 shows the preparation step it is on and its calls done", () => {
    renderRun();
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "running", "not run", "not run", "not run"]),
      preparation: [{ title: "The items discussed", done: 1, total: 1 }, { title: "Each item in detail", done: 5, total: 9 },
        { title: "The sprint as committed", done: 0, total: 1 }] });
    const bar = document.querySelector<HTMLElement>(".card.frames")!;
    expect(within(bar).getByText("Preparing the notes")).toBeInTheDocument();
    expect(bar.textContent).toContain("Each item in detail · step 2 of 3");
    expect(within(bar).getByText("accepted 5", { exact: false })).toBeInTheDocument();
    expect(within(bar).getByText("waiting 4", { exact: false })).toBeInTheDocument();
  });

  it("phase 2 shows the stage it is on, how far it is and its pace, then the next stage", () => {
    renderRun();
    const rows = makePhaseRows(["complete", "running", "not run", "not run", "not run", "not run"]);
    const stage = (name: string, unit: string, status: "waiting" | "running" | "done", done: number, total: number | null, seconds: number) =>
      ({ name, unit, status, done, total, frames: null, seconds });
    sendUpdate({ phases: rows, frames_preparing: [stage("rules", "seconds of video", "running", 1200, 3000, 156),
      stage("classifier", "frames", "waiting", 0, null, 0), stage("model", "frames", "waiting", 0, null, 0)] });
    let bar = document.querySelector<HTMLElement>(".card.frames")!;
    expect(within(bar).getByText("Preparing frames")).toBeInTheDocument();
    expect(bar.textContent).toContain("rules · stage 1 of 3 · seconds of video · 3000");
    expect(within(bar).getByText("done 1200", { exact: false })).toBeInTheDocument();
    expect(within(bar).getByText("waiting 1800", { exact: false })).toBeInTheDocument();
    expect(within(bar).getByText("One second of video takes 0.13 s on average")).toHaveClass("pace");
    sendUpdate({ phases: rows, frames_preparing: [{ ...stage("rules", "seconds of video", "done", 3000, 3000, 390), frames: 240, kept: 240 },
      { ...stage("classifier", "frames", "done", 240, 240, 150), frames: 240, kept: 200 }, stage("model", "frames", "running", 40, 240, 80)] });
    bar = document.querySelector<HTMLElement>(".card.frames")!;
    expect(bar.textContent).toContain("model · stage 3 of 3 · frames · 240");
    expect(within(bar).getByText("One frame takes 2.00 s on average")).toHaveClass("pace");
    // the title opens the list of stages: each with its status, frames in, removed, left, time and time per frame
    expect(screen.queryByRole("table", { name: "stages of preparing the frames" })).not.toBeInTheDocument();
    fireEvent.click(within(bar).getByRole("button", { name: "show stage details" }));
    const rowsShown = within(screen.getByRole("table", { name: "stages of preparing the frames" })).getAllByRole("row")
      .map((row) => row.textContent);
    expect(rowsShown).toEqual(["StageStatusFrames inRemovedLeftTimeTime per frame", "1. rulesdone--2406m 30s1.63 s",
      "2. classifierdone240402002m 30s0.63 s", "3. redundancy modelrunning · 40 of 240240--1m 20s2.00 s"]);
    fireEvent.click(within(bar).getByRole("button", { name: "hide stage details" }));
    expect(screen.queryByRole("table", { name: "stages of preparing the frames" })).not.toBeInTheDocument();
  });

  it("a filter's removed count comes from its dropped frames and opens them, each with what holds it and why", () => {
    renderRun();
    const rows = makePhaseRows(["complete", "complete", "not run", "not run", "not run", "not run"]);
    // a run whose stage record kept no counts: the removed frames still say what each filter removed
    sendUpdate({ phases: rows, frames: { pending: 59, analyzed: 0, written: 0, skipped: 0, blocked: 0 },
      frames_preparing: [{ name: "rules", unit: "seconds of video", status: "done", done: 900, total: 900, frames: 61, seconds: 90 },
        { name: "classifier", unit: "frames", status: "done", done: 61, total: 61, frames: 61, seconds: 20, removed: [] },
        { name: "model", unit: "frames", status: "done", done: 61, total: 61, frames: 61, seconds: 120,
          removed: [{ slug: "frame-004", path: "frames/dropped/frame-004.jpg", covered_by: ["frame-005"], reason: "the same board" },
            { slug: "frame-009", path: "frames/dropped/frame-009.jpg", covered_by: ["frame-010"], reason: "" }] },
        { name: "similarity", unit: "frames", status: "done", done: 6, total: 6, frames: 6, seconds: 30,
          removed: [{ slug: "frame-030", path: "frames/dropped/frame-030.jpg", covered_by: ["frame-041"], reason: "shown again" }] }] });
    const bar = document.querySelector<HTMLElement>(".card.frames")!;
    fireEvent.click(within(bar).getByRole("button", { name: "show stage details" }));
    const table = screen.getByRole("table", { name: "stages of preparing the frames" });
    expect(within(table).getAllByRole("row").map((row) => row.textContent)).toEqual([
      "StageStatusFrames inRemovedLeftTimeTime per frame", "1. rulesdone--611m 30s1.48 s",
      "2. classifierdone61061-0.33 s".replace("-", "20s"), "3. redundancy modeldone612592m 00s1.97 s",
      "4. similarity recheckdone61530s5.00 s"]);
    fireEvent.click(within(table).getByRole("button", { name: "2" }));
    const shown = within(table).getByRole("list", { name: "frames redundancy model removed" });
    expect(within(shown).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "frame-004held by frame-005the same board", "frame-009held by frame-010"]);
    expect(within(shown).getByAltText("frame-004")).toHaveAttribute("src", expect.stringContaining("frames/dropped/frame-004.jpg"));
    fireEvent.click(within(table).getByRole("button", { name: "2" }));
    expect(within(table).queryByRole("list")).not.toBeInTheDocument();
  });

  it("a run stopped after phase 2 shows phase 2's bar and marks its card, never the next phase's", () => {
    renderRun();
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "not run", "not run", "not run", "not run"]),
      frames: { pending: 52, analyzed: 0, written: 0, skipped: 0, blocked: 0 },
      frames_preparing: [{ name: "rules", unit: "seconds of video", status: "done", done: 1898, total: 1898, frames: 73, seconds: 226 },
        { name: "classifier", unit: "frames", status: "done", done: 73, total: 73, frames: 73, seconds: 38 },
        { name: "model", unit: "frames", status: "done", done: 67, total: 67, frames: 67, seconds: 117 }] });
    expect(document.querySelector(".card.frames")!.textContent).toContain("Preparing frames");
    // a finished phase 2 counts what it kept and dropped of the frames its rules took, never its first stage
    const finished = document.querySelector<HTMLElement>(".card.frames")!;
    expect(finished.textContent).toContain("3 stages done · frames the rules took · 73");
    expect(within(finished).getByText("kept 52", { exact: false })).toBeInTheDocument();
    expect(within(finished).getByText("dropped 21", { exact: false })).toBeInTheDocument();
    const shown = [...document.querySelectorAll(".step.shown")].map((element) => element.textContent ?? "");
    expect(shown.length).toBe(1);
    expect(shown[0]).toMatch(/Frames Preparing|frames preparing/i);
  });

  it("phase 1 counts its stages and opens the list of them: the transcription, then the speakers", () => {
    renderRun();
    sendUpdate({ phases: makePhaseRows(["running", "not run", "not run", "not run", "not run", "not run"]),
      transcribing: [{ name: "transcription", unit: "steps", status: "done", done: 1, total: null, frames: null, seconds: 300 },
        { name: "speakers", unit: "steps", status: "running", done: 0, total: null, frames: null, seconds: 45 }] });
    const bar = document.querySelector<HTMLElement>(".card.frames")!;
    expect(bar.textContent).toContain("Transcribing");
    expect(bar.textContent).toContain("speakers · stage 2 of 2");
    fireEvent.click(within(bar).getByRole("button", { name: "show stage details" }));
    const rowsShown = within(screen.getByRole("table", { name: "stages of transcribing" })).getAllByRole("row")
      .map((row) => row.textContent);
    expect(rowsShown).toEqual(["StageStatusFrames inRemovedLeftTimeTime per frame", "1. transcriptiondone---5m 00s-",
      "2. speakersrunning---45s-"]);
  });

  it("phase 1 counts a PDF's scanned pages as they are read defect", () => {
    // Defect: while Unlimited-OCR-2 read a PDF's scans, 8 to 20 s a page, phase 1's bar showed "waiting 1" for minutes
    // Fix: the transcription stage counts its scanned pages, and the bar shows them read of all, with a pace
    renderRun();
    sendUpdate({ phases: makePhaseRows(["running", "not run", "not run", "not run", "not run", "not run"]),
      transcribing: [{ name: "transcription", unit: "scanned pages", status: "running", done: 12, total: 40, frames: null,
        seconds: 180 }] });
    const bar = document.querySelector<HTMLElement>(".card.frames")!;
    expect(bar.textContent).toContain("12 of 40 scanned pages");
    expect(bar.textContent).toContain("One page takes 15s on average");
    expect(within(bar).getByText("done 12", { exact: false })).toBeInTheDocument();
    expect(within(bar).getByText("waiting 28", { exact: false })).toBeInTheDocument();
  });

  it("phase 1 shows a PDF's reading step by step, with its time left and an orange last progress when stalled defect", () => {
    // Defect: a 6,761-page PDF stood at 6,761 of 6,761 for hours, with no sign of its step or whether it still moved
    // Fix: each step is its own row in pages, the bar names the step with its time left, and "Last progress" turns
    // orange after 10 minutes with no page counted
    renderRun();
    const waiting = { status: "waiting" as const, done: 0, total: null, frames: null, seconds: 0 };
    sendUpdate({ phases: makePhaseRows(["running", "not run", "not run", "not run", "not run", "not run"]),
      transcribing: [
        { name: "text layer", unit: "pages", status: "done", done: 6761, total: 6761, frames: null, seconds: 600 },
        { name: "figures", unit: "pages", status: "done", done: 6761, total: 6761, frames: null, seconds: 1200 },
        { name: "parsing", unit: "pages", status: "done", done: 6761, total: 6761, frames: null, seconds: 33000 },
        { name: "markdown", unit: "pages", status: "running", done: 1200, total: 6761, frames: null, seconds: 3600,
          seconds_left: 16683, seconds_since_progress: 720 },
        { name: "scanned pages", unit: "pages", ...waiting }, { name: "page cleaning", unit: "pages", ...waiting }] });
    const bar = document.querySelector<HTMLElement>(".card.frames")!;
    expect(bar.textContent).toContain("writing markdown · stage 4 of 6 · 1,200 of 6,761 pages");
    expect(bar.textContent).toContain("left");
    const last = within(bar).getByText(/Last progress 12m/);
    expect(last).toHaveClass("stalled");
    fireEvent.click(within(bar).getByRole("button", { name: "show stage details" }));
    const rowsShown = within(screen.getByRole("table", { name: "stages of reading the PDF" })).getAllByRole("row")
      .map((row) => row.textContent);
    expect(rowsShown[0]).toBe("StageStatusPagesTimeTime per pageTime left");
    expect(rowsShown[4]).toContain("4. writing markdownrunning1,200 of 6,761");
  });

  it("phase 7 shows its parts done, running and waiting, with the time left, and a table of parts defect", () => {
    // Defect: a knowledge pass's progress said "this phase records no steps to count", though its parts are known
    // Fix: the bar counts the parts, names the running one with the time left, and its table lists each part
    renderRun();
    sendUpdate({ document_pass: "knowledge",
      phases: [...makePhaseRows(["complete", "complete", "off", "complete", "off", "off"]),
        { number: 7, name: "document_reshaping", status: "running", title: "LLM knowledge reshaping" }],
      document_pass_parts: [
        { number: 1, status: "done", blocks: 1046, tokens: 52300, sections: 41, calls: 9, seconds: 1800 },
        { number: 2, status: "running", blocks: 662, tokens: 50100, sections: 18, figures: 7, calls: 3, compactions: 2,
          prunes: 1, tokens_per_second: 38.4, seconds: 600 },
        { number: 3, status: "waiting", blocks: 1047, tokens: 51800, sections: 37, calls: 0, seconds: 0 }] });
    const bar = document.querySelector<HTMLElement>(".card.frames")!;
    expect(bar.textContent).toContain("Reshaping knowledge");
    expect(bar.textContent).toContain("part 2 of 3");
    expect(bar.textContent).toContain("One part takes 30m 00s on average · about 50m 00s left");
    expect(within(bar).getByText("waiting 1", { exact: false })).toBeInTheDocument();
    fireEvent.click(within(bar).getByRole("button", { name: "show part details" }));
    const rowsShown = within(screen.getByRole("table", { name: "parts of the document pass" })).getAllByRole("row")
      .map((row) => row.textContent);
    // each part's size in tokens and sections, never in blocks, a word the page shows nowhere else
    // each part's compactions beside its calls (the owner: "i want to see number of compaction somewhere")
    // and its figures and prunes, each on a cell's second line, so the table keeps five columns (the owner: "not make the
    // table much bigger with more fields")
    expect(rowsShown[0]).toBe("PartStatusSizeCallsTime");
    expect(rowsShown[2]).toBe("part 2runninglive50,100 tokens18 sections · 7 figures32 compactions · 1 prune10m 00s38.4 tok/s");
    expect(rowsShown[1]).toBe("part 1done52,300 tokens41 sections · 0 figures930m 00s");
    expect(rowsShown[3]).toBe("part 3waiting51,800 tokens37 sections · 0 figures--");
  });

  it("a finished phase's card shows its bar when clicked, and the running phase's again when clicked twice", () => {
    renderRun();
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "complete", "running", "not run", "not run"]),
      frames: { pending: 0, analyzed: 0, written: 150, skipped: 2, blocked: 0 },
      document_stages: [{ name: "chapters", counts: { accepted: 3, running: 1 } }] });
    const card = (name: RegExp) => [...document.querySelectorAll<HTMLElement>(".step-choose")].find((element) =>
      name.test(element.textContent ?? ""))!;
    expect(document.querySelector(".card.frames")!.textContent).toContain("Writing the chapters");
    fireEvent.click(card(/Frames Processing|frames processing/i));
    expect(document.querySelector(".card.frames")!.textContent).toContain("Frames");
    expect(document.querySelector(".card.frames")!.textContent).toContain("152");
    expect(card(/Frames Processing|frames processing/i)).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(card(/Frames Processing|frames processing/i));
    expect(document.querySelector(".card.frames")!.textContent).toContain("Writing the chapters");
  });

  it("a clicked phase with no steps to count says so, and its help button chooses nothing", () => {
    renderRun();
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "running", "not run", "not run", "not run"]),
      frames: { pending: 10, analyzed: 0, written: 5, skipped: 0, blocked: 0 } });
    const transcribe = [...document.querySelectorAll<HTMLElement>(".stepper .step")].find((element) =>
      /transcribe/i.test(element.textContent ?? ""))!;
    const choose = within(transcribe).getByRole("button", { name: /transcribe/i });
    // the help is a button of its own beside the card's button, so opening it chooses nothing
    fireEvent.click(within(transcribe).getByTitle("what phase 1 does"));
    expect(choose).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(choose);
    expect(screen.getByText("this phase records no steps to count: its time is on its card")).toBeInTheDocument();
  });

  it("phase 2's classifier says it counts its frames only once it ends", () => {
    renderRun();
    sendUpdate({ phases: makePhaseRows(["complete", "running", "not run", "not run", "not run", "not run"]),
      frames_preparing: [{ name: "rules", unit: "seconds of video", status: "done", done: 3000, total: 3000, frames: 240, seconds: 390 },
        { name: "classifier", unit: "frames", status: "running", done: 0, total: 240, frames: null, seconds: 20 }] });
    const bar = document.querySelector<HTMLElement>(".card.frames")!;
    expect(bar.textContent).toContain("classifier · stage 2 of 2");
    expect(bar.textContent).toContain("the classifier counts its frames only once it ends");
    expect(document.querySelector(".legend .pace")).toBeNull();
  });

  it("the Frames bar says how long one frame is taking, and says nothing before the first one is done", () => {
    renderRun();
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "running", "not run", "not run", "not run"]),
      frames: { pending: 109, analyzed: 0, written: 43, skipped: 0, blocked: 0 }, seconds_per_frame: 26 });
    const bar = document.querySelector<HTMLElement>(".card.frames")!;
    expect(within(bar).getByText("One frame takes 26s on average")).toHaveClass("pace");
    expect(within(bar).getByText("152 in all")).toBeInTheDocument();
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "running", "not run", "not run", "not run"]),
      frames: { pending: 152, analyzed: 0, written: 0, skipped: 0, blocked: 0 }, seconds_per_frame: 0 });
    expect(document.querySelector<HTMLElement>(".card.frames")!.textContent).toContain("152");
    expect(document.querySelector(".legend .pace")).toBeNull();
  });
});

describe("RunDetail with a phase switched off", () => {
  it("grays out phase 5 as off and still calls a run whose other phases are complete done", () => {
    const view = render(<RunDetail run={makeRun()} phases={PHASES.map((phase) => phase.number === 5 ? { ...phase, is_on: false } : phase)}
      jobs={[]} onBack={vi.fn()} onChanged={vi.fn()} onMessage={vi.fn()} />);
    sendUpdate({ status: "complete", phases: makePhaseRows(["complete", "complete", "complete", "complete", "not run", "complete"]) });
    const review = view.container.querySelectorAll(".stepper .step")[4];
    expect(review).toHaveClass("off");
    expect(review.querySelector(".step-choose")).toHaveAttribute("title", "skipped: phase 5 is switched off in the settings");
    expect(within(review as HTMLElement).getByText("skipped")).toBeInTheDocument();
    expect(within(toolbar()).queryByText(/Resume from/)).not.toBeInTheDocument();
  });

  it("offers Resume for a knowledge pass interrupted in phase 7, which the settings keep off, and no stale stop defect", () => {
    // The owner interrupted a phase 7 knowledge pass and the run's page showed no Resume and still said "stop requested":
    // phase 7, off in the settings unless a run asks for a pass, counted as off for this run too. A run that asked for a
    // pass has phase 7 on, and a request left in an ended run's file is not shown
    const withPass = [...PHASES, { number: 7, name: "document_reshaping", title: "document reshaping", is_on: false }];
    render(<RunDetail run={makeRun()} phases={withPass} jobs={[]} onBack={vi.fn()} onChanged={vi.fn()} onMessage={vi.fn()} />);
    sendUpdate({ status: "interrupted", signal: "stop", document_pass: "knowledge", phases: [
      ...makePhaseRows(["complete", "complete", "off", "complete", "off", "off"]),
      { number: 7, name: "document_reshaping", status: "not run", title: "document reshaping" }] });
    expect(within(toolbar()).getByRole("button", { name: /Resume from 7/ })).toBeInTheDocument();
    expect(screen.queryByText("stop requested")).not.toBeInTheDocument();
  });

  it("shows a phase the run recorded off as off even with its switch on again", () => {
    renderRun();
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "complete", "complete", "off", "not run"]) });
    expect(document.querySelectorAll(".stepper .step")[4]).toHaveClass("off");
    expect(document.querySelectorAll(".stepper .step")[4].querySelector(".step-choose"))
      .toHaveAttribute("title", "skipped: phase 5 is switched off in the settings");
  });
});

describe("RunDetail compress recording", () => {
  const DONE = makePhaseRows(["complete", "complete", "complete", "complete", "complete", "complete"]);

  it("offers Compress to 720p for a kept recording, starts it, and shows it working with the Recording menu away", async () => {
    const calls = installServer({ [`POST ${RUN_BASE}/recording/compress`]: { pid: 77 } });
    const { onMessage } = renderRun();
    sendUpdate({ status: "complete", is_recording_kept: true, has_recording: true, phases: DONE });
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Compress to 720p/ }));
    expect(calls.some((call) => call.method === "POST" && call.url === `${RUN_BASE}/recording/compress`)).toBe(true);
    expect(onMessage).toHaveBeenCalledWith("AcmeSync-demo: compressing the recording to 720p", false);
    sendUpdate({ status: "complete", is_recording_kept: true, has_recording: true, phases: DONE,
      recording_compression: { status: "running", error: "", before_bytes: 8e9, after_bytes: 0, files: 0, progress: 0 } });
    expect(within(toolbar()).getByText(/Compressing recording to 720p/).querySelector(".spinner")).not.toBeNull();
    sendUpdate({ status: "complete", is_recording_kept: true, has_recording: true, phases: DONE,
      recording_compression: { status: "running", error: "", before_bytes: 8e9, after_bytes: 0, files: 0, progress: 0.42 } });
    expect(within(toolbar()).getByText(/Compressing recording to 720p/)).toHaveTextContent("Compressing recording to 720p… 42%");
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    expect(screen.queryByRole("menuitem", { name: /Compress to 720p/ })).not.toBeInTheDocument();
  });

  it("says how much smaller it got and stops offering it once done", async () => {
    renderRun();
    sendUpdate({ status: "complete", is_recording_kept: true, has_recording: true, phases: DONE,
      recording_compression: { status: "done", error: "", before_bytes: 8.1e9, after_bytes: 9e8, files: 1, progress: 0 } });
    expect(within(toolbar()).getByText("Recording compressed · 8.1 GB → 900 MB")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    expect(screen.queryByRole("menuitem", { name: /Compress to 720p/ })).not.toBeInTheDocument();
  });

  it("says a failed compression failed, with ffmpeg's error on hover, and offers it again", async () => {
    renderRun();
    sendUpdate({ status: "complete", is_recording_kept: true, has_recording: true, phases: DONE,
      recording_compression: { status: "failed", error: "ffmpeg failed on a.mp4", before_bytes: 0, after_bytes: 0, files: 0, progress: 0 } });
    expect(within(toolbar()).getByText("Compressing the recording failed")).toHaveAttribute("title", "ffmpeg failed on a.mp4");
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    expect(screen.getByRole("menuitem", { name: /Compress to 720p/ })).toBeInTheDocument();
  });

  it("offers nothing to compress while the recording is in the inbox", async () => {
    renderRun();
    sendUpdate({ status: "complete", is_recording_kept: false, can_keep_recording: true, has_recording: true, phases: DONE });
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    expect(screen.queryByRole("menuitem", { name: /Compress to 720p/ })).not.toBeInTheDocument();
  });
});

describe("RunDetail settings changed under a running run", () => {
  it("says so while the run is live, and says nothing once it has stopped", () => {
    renderRun();
    sendUpdate({ live_pids: [41], status: "running", has_newer_settings: true,
      phases: makePhaseRows(["complete", "complete", "running", "not run", "not run", "not run"]) });
    expect(screen.getByRole("status")).toHaveTextContent("Settings changed after this run started");
    expect(screen.getByRole("status")).toHaveTextContent("stop it and resume it to use the new ones");
    sendUpdate({ live_pids: [], status: "incomplete", has_newer_settings: true,
      phases: makePhaseRows(["complete", "complete", "incomplete", "not run", "not run", "not run"]) });
    expect(screen.queryByText(/Settings changed after this run started/)).not.toBeInTheDocument();
  });
});

describe("RunDetail profile", () => {
  it("says an audio run's profile is still to be chosen, then names it the moment phase 2 picks it", async () => {
    installServer({ "GET /api/ingesting-profiles": { profiles: [] } });
    renderRun({ run: makeRun({ is_audio: true, is_profile_chosen: false, ingesting_profile: "knowledge" }) });
    sendUpdate({ is_audio: true, is_profile_chosen: false, ingesting_profile: "knowledge",
      phases: makePhaseRows(["complete", "running", "not run", "not run", "not run"]) });
    expect(await screen.findByText("Ingesting profile: chosen in phase 2")).toBeInTheDocument();
    sendUpdate({ is_audio: true, is_profile_chosen: true, ingesting_profile: "presentation" });
    expect(await screen.findByText("Ingesting profile: presentation")).toBeInTheDocument();
  });

  it("a video run shows its settings profile and no ingesting profile", async () => {
    installServer({ "GET /api/ingesting-profiles": { profiles: [] } });
    renderRun({ run: makeRun({ is_audio: false, ingesting_profile: "knowledge" }) });
    sendUpdate({ is_audio: false, ingesting_profile: "knowledge", settings_profile: "strict" });
    expect(await screen.findByText("Settings: strict")).toBeInTheDocument();
    expect(screen.queryByText(/Ingesting profile/)).not.toBeInTheDocument();
    sendUpdate({ is_audio: false, ingesting_profile: "knowledge", settings_profile: "" });
    expect(await screen.findByText("Settings: default")).toBeInTheDocument();
  });
});

describe("RunDetail branch", () => {
  it("offers each phase up to the one after the last reached, sends the phase and opens the new run", async () => {
    const calls = installServer({ [`POST ${RUN_BASE}/branch`]: { exit_code: 0, output: "branched",
      records: [{ name: "AcmeSync-demo-from-3", folder: "/out/AcmeSync-demo-from-3", phase: 3 }] } });
    const onOpenRun = vi.fn();
    const onMessage = vi.fn();
    render(<RunDetail run={makeRun()} phases={PHASES} jobs={[]} onBack={vi.fn()} onChanged={vi.fn()} onMessage={onMessage}
      onOpenRun={onOpenRun} />);
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "blocked", "not run", "not run", "not run"]) });
    await userEvent.click(screen.getByRole("button", { name: "Copy and run again" }));
    const offered = screen.getAllByRole("menuitem").map((item) => item.textContent ?? "").filter((text) => text.startsWith("Branch"));
    expect(offered.map((text) => text.split(" · ")[0])).toEqual(["Branch from phase 2", "Branch from phase 3", "Branch from phase 4"]);
    await userEvent.click(screen.getByRole("menuitem", { name: /Branch from phase 3/ }));
    expect(calls.find((call) => call.url === `${RUN_BASE}/branch`)?.body).toEqual({ phase: 3 });
    expect(onMessage).toHaveBeenLastCalledWith("AcmeSync-demo branched from phase 3 into AcmeSync-demo-from-3; press Resume there to run it", false);
    expect(onOpenRun).toHaveBeenCalledWith("knowledge", "AcmeSync-demo-from-3");
  });

  it("a PDF run shows its processing mode and branches from phase 2 with the other one", async () => {
    const calls = installServer({ [`POST ${RUN_BASE}/branch`]: { exit_code: 0, output: "branched",
      records: [{ name: "AcmeSync-demo-from-2", folder: "/out/AcmeSync-demo-from-2", phase: 2 }] } });
    const onOpenRun = vi.fn();
    render(<RunDetail run={makeRun()} phases={PHASES} jobs={[]} onBack={vi.fn()} onChanged={vi.fn()} onMessage={vi.fn()}
      onOpenRun={onOpenRun} />);
    sendUpdate({ pdf_mode: "pages-as-images", phases: makePhaseRows(["complete", "complete", "complete", "not run", "not run", "not run"]) });
    await userEvent.click(screen.getByRole("button", { name: "Copy and run again" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Text · LLM-written document/ }));
    expect(calls.find((call) => call.url === `${RUN_BASE}/branch`)?.body).toEqual({ phase: 2, pdf_mode: "text-only" });
    expect(onOpenRun).toHaveBeenCalledWith("knowledge", "AcmeSync-demo-from-2");
  });

  it("the help inside the Copy and run again menu says what each section does", async () => {
    installServer({});
    render(<RunDetail run={makeRun()} phases={PHASES} jobs={[]} onBack={vi.fn()} onChanged={vi.fn()} onMessage={vi.fn()}
      onOpenRun={vi.fn()} />);
    sendUpdate({ pdf_mode: "text-only", phases: makePhaseRows(["complete", "complete", "complete", "not run", "not run", "not run"]) });
    await userEvent.click(screen.getByRole("button", { name: "Copy and run again" }));
    expect(screen.queryByText(/reads the PDF again as text or as page pictures/)).not.toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("menu")).getByRole("button", { name: "explain this menu" }));
    expect(screen.getByText(/linked, not copied/)).toBeInTheDocument();
    expect(screen.getByText(/reads the PDF again as text or as page pictures/)).toBeInTheDocument();
    expect(screen.getByText(/press Resume there to run them/)).toBeInTheDocument();
  });

  it("a PDF run read as text only offers the markdown conversion beside pages as images defect", async () => {
    // The Copy and run again menu offered one other mode, so a text-only run could never be converted to markdown.
    // Now every mode but the run's own is offered.
    const calls = installServer({ [`POST ${RUN_BASE}/branch`]: { exit_code: 0, output: "branched",
      records: [{ name: "AcmeSync-demo-from-2", folder: "/out/AcmeSync-demo-from-2", phase: 2 }] } });
    render(<RunDetail run={makeRun()} phases={PHASES} jobs={[]} onBack={vi.fn()} onChanged={vi.fn()} onMessage={vi.fn()}
      onOpenRun={vi.fn()} />);
    sendUpdate({ pdf_mode: "text-only", phases: makePhaseRows(["complete", "complete", "complete", "not run", "not run", "not run"]) });
    await userEvent.click(screen.getByRole("button", { name: "Copy and run again" }));
    const offered = screen.getAllByRole("menuitem").map((item) => item.textContent ?? "").filter((text) => text.includes(" · ")
      && !text.startsWith("Branch"));
    expect(offered.map((text) => text.split("the ")[0].trim())).toEqual(["Pages as images · LLM-written document", "Text · Markdown"]);
    // a PDF is never offered the profiles of people speaking
    expect(screen.queryByRole("menuitem", { name: /Convert to/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitem", { name: /Text · Markdown/ }));
    expect(calls.find((call) => call.url === `${RUN_BASE}/branch`)?.body).toEqual({ phase: 2, pdf_mode: "markdown-conversion" });
  });

  it("a PDF run is offered no other PDF reader", async () => {
    installServer({});
    render(<RunDetail run={makeRun()} phases={PHASES} jobs={[]} onBack={vi.fn()} onChanged={vi.fn()} onMessage={vi.fn()}
      onOpenRun={vi.fn()} />);
    sendUpdate({ pdf_mode: "pages-as-images",
      phases: makePhaseRows(["complete", "complete", "complete", "complete", "complete", "complete"]) });
    await userEvent.click(screen.getByRole("button", { name: "Copy and run again" }));
    expect(screen.getByRole("menuitem", { name: /Text · Markdown/ })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: /Read with/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Another PDF reader")).not.toBeInTheDocument();
  });

  it("a run of another recording shows no processing mode", () => {
    renderRun();
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "complete", "not run", "not run", "not run"]) });
    expect(screen.queryByText(/Processing mode/)).not.toBeInTheDocument();
  });

  it("a video run's header names its Workflow speed: its kept one, else Fast for a run made on the fast workflow", () => {
    renderRun();
    sendUpdate({ workflow: "fast-processing" });
    expect(document.querySelector(".speed-pill")).toHaveTextContent("Speed: Fast");
    sendUpdate({ workflow: "quality", speed: { preset: "balanced", values: {}, name: "custom" } });
    expect(document.querySelector(".speed-pill")).toHaveTextContent("Speed: Custom");
  });

  it("a run kept as Balanced with no change by hand reads Balanced, and Balanced chosen again in the chip is kept in the run", async () => {
    // The defect: the chip read Custom for a Balanced run holding an old copy of Balanced's values, and choosing Balanced
    // again was held on the page alone, so a reload gave Custom back
    const calls = installServer({ "GET /api/run-defaults?profile=": { pdf_mode: "text-only", pdf_ocr_engine: "tesseract",
      knowledge_filter_model: "", workflow: "quality", speed: SPEED_OPTIONS },
      [`POST ${RUN_BASE}/speed`]: { preset: "balanced", values: {}, name: "balanced", changes: {} } });
    renderRun();
    const oldCopy = { ...SPEED_OPTIONS.presets.balanced, "review.isOn": false, "framesProcessing.isCritiqueOn": true };
    sendUpdate({ workflow: "quality", speed: { preset: "balanced", values: oldCopy, name: "custom", changes: {} } });
    expect(await screen.findByRole("button", { name: "workflow speed: Balanced" })).toBeInTheDocument();
    sendUpdate({ workflow: "quality", speed: { preset: "balanced", values: oldCopy, name: "custom" } });
    await userEvent.click(await screen.findByRole("button", { name: "workflow speed: Custom" }));
    await userEvent.click(screen.getByRole("radio", { name: /^Balanced/ }));
    const saved = calls.find((call) => call.method === "POST" && call.url === `${RUN_BASE}/speed`);
    expect(saved?.body).toEqual({ speed_preset: "balanced", speed_settings: {} });
    expect(screen.getByRole("button", { name: "workflow speed: Balanced" })).toBeInTheDocument();
  });

  it("a branched run's header names the run and phase it came from", () => {
    renderRun();
    sendUpdate({ branch: { from: "AcmeSync-demo", phase: 3, at: "2026-10-16T20:00:00" } });
    expect(screen.getByRole("button", { name: "branched from AcmeSync-demo" }).closest("small")).toHaveTextContent("branched from AcmeSync-demo at phase 3");
  });

  it("says why when the branch is refused, and opens nothing", async () => {
    installServer({ [`POST ${RUN_BASE}/branch`]: { exit_code: 2, output: "a run writes into this folder" } });
    const onOpenRun = vi.fn();
    const onMessage = vi.fn();
    render(<RunDetail run={makeRun()} phases={PHASES} jobs={[]} onBack={vi.fn()} onChanged={vi.fn()} onMessage={onMessage}
      onOpenRun={onOpenRun} />);
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "not run", "not run", "not run", "not run"]) });
    await userEvent.click(screen.getByRole("button", { name: "Copy and run again" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Branch from phase 2/ }));
    expect(onMessage).toHaveBeenLastCalledWith("a run writes into this folder", true);
    expect(onOpenRun).not.toHaveBeenCalled();
  });
});

describe("RunDetail links", () => {
  it("opens the critique's rule set, the published document, and the run a branch came from", async () => {
    installServer({ [`GET ${RUN_BASE}/artifacts`]: [] });
    const onOpenRun = vi.fn();
    render(<RunDetail run={makeRun()} phases={PHASES} jobs={[]} onBack={vi.fn()} onChanged={vi.fn()} onMessage={vi.fn()}
      onOpenRun={onOpenRun} />);
    sendUpdate({ status: "complete", phases: makePhaseRows(["complete", "complete", "complete", "complete", "complete"]),
      has_document: true, document_file: "gateway-login/gateway-login.md", branch: { from: "AcmeSync-original", phase: 3, at: "2026-10-16T20:00:00" },
      critique: { files: 1, rules: 2, anti_patterns: 1, top: "a rule", top_batches: 1, top_frames: 2,
        rule_set: "06_critique-collection/critique-collection.md" } });
    await userEvent.click(screen.getByRole("button", { name: "more ways to get the document" }));
    expect(screen.getByRole("menuitem", { name: /Download document/ })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: "branched from AcmeSync-original" }));
    expect(onOpenRun).toHaveBeenCalledWith("knowledge", "AcmeSync-original");
    await userEvent.click(screen.getByRole("button", { name: /Open the rule set/ }));
    expect(screen.getByRole("tab", { name: "Artifacts" })).toHaveAttribute("aria-selected", "true");
  });

  it("takes a run waiting in the queue off it from its own page", async () => {
    const calls = installServer({ "POST /api/jobs/job-4/cancel": makeJob({ job_id: "job-4", status: "cancelled" }) });
    renderRun({ jobs: [makeJob({ job_id: "job-4", status: "queued" }, { output_root: "/work/knowledge/AcmeSync-demo" })] });
    sendUpdate({});
    await userEvent.click(screen.getByRole("button", { name: "Take off the queue" }));
    expect(calls.some((call) => call.url === "/api/jobs/job-4/cancel")).toBe(true);
  });

  it("opens the new run folder a conversion made", async () => {
    installServer({ "GET /api/ingesting-profiles": { profiles: [{ name: "knowledge", aliases: [] }, { name: "presentation", aliases: [] }] },
      [`POST ${RUN_BASE}/convert`]: { exit_code: 0, output: "copied", jobs: [{ job_id: "job-c" }],
        records: [{ folder: "/work/knowledge/AcmeSync-demo-presentation" }] } });
    const onOpenRun = vi.fn();
    render(<RunDetail run={makeRun({ has_document: true, ingesting_profile: "knowledge" })} phases={PHASES} jobs={[]} onBack={vi.fn()}
      onChanged={vi.fn()} onMessage={vi.fn()} onOpenRun={onOpenRun} />);
    sendUpdate({ has_document: true, ingesting_profile: "knowledge" });
    await userEvent.click(screen.getByRole("button", { name: "Copy and run again" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Convert to presentation/ }));
    expect(onOpenRun).toHaveBeenCalledWith("knowledge", "AcmeSync-demo-presentation");
  });
});

describe("RunDetail convert", () => {
  it("offers a finished run's document in every other ingesting profile, and Convert copies it and queues phase 4", async () => {
    const calls = installServer({
      "GET /api/ingesting-profiles": { profiles: [{ name: "knowledge", aliases: [] }, { name: "conversation", aliases: ["chat"] },
        { name: "presentation", aliases: [] }] },
      // the server names the new folder by its path; the message names it as the server made it
      [`POST ${RUN_BASE}/convert`]: { exit_code: 0, output: "copied", jobs: [makeJob({ job_id: "job-c" })],
        records: [{ folder: "/work/knowledge/AcmeSync-demo-presentation", profile: "presentation" }] },
    });
    const { onMessage, onChanged } = renderRun({ run: makeRun({ has_document: true, ingesting_profile: "conversation",
      is_audio: true, is_profile_chosen: true }) });
    sendUpdate({ has_document: true, ingesting_profile: "conversation", is_audio: true, is_profile_chosen: true });
    // An audio run's header names the profile the document is written in; Convert is a group of the Copy and run again menu
    expect(await screen.findByText("Ingesting profile: conversation")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Copy and run again" }));
    const offered = screen.getAllByRole("menuitem").map((item) => item.textContent ?? "").filter((text) => text.startsWith("Convert"));
    // One help line above the profiles says what every conversion does; no profile repeats it under its name
    expect(offered).toEqual(["Convert to knowledge", "Convert to presentation"]);
    expect(screen.getByText("Another profile")).toHaveClass("menu-group");
    await userEvent.click(screen.getByRole("menuitem", { name: /Convert to presentation/ }));
    expect(calls.find((call) => call.url === `${RUN_BASE}/convert`)?.body).toEqual({ profile: "presentation" });
    expect(onMessage).toHaveBeenLastCalledWith("AcmeSync-demo copied to AcmeSync-demo-presentation; phase 4 queued (job job-c)", false);
    expect(onChanged).not.toHaveBeenCalled();
  });

  it("offers no conversion before the run has a document", async () => {
    installServer({ "GET /api/ingesting-profiles": { profiles: [{ name: "knowledge", aliases: [] }, { name: "meeting", aliases: [] }] } });
    renderRun();
    sendUpdate({ has_document: false });
    await userEvent.click(screen.getByRole("button", { name: "more actions" }));
    expect(screen.queryByRole("menuitem", { name: /Convert/ })).not.toBeInTheDocument();
  });
});

describe("RunDetail and what the worker saw", () => {
  it("the bar of a phase that calls a model opens what its worker was sent, and closes again", async () => {
    const user = userEvent.setup();
    installServer({ [`GET ${RUN_BASE}/worker?phase=3`]: { steps: [] } });
    renderRun();
    sendUpdate({ phases: makePhaseRows(["complete", "complete", "running", "not run", "not run", "not run"]),
      frames: { pending: 10, analyzed: 0, written: 2, skipped: 0, blocked: 0 }, seconds_per_frame: 20 });
    const bar = document.querySelector<HTMLElement>(".card.frames")!;
    await user.click(within(bar).getByRole("button", { name: /What the worker saw/ }));
    expect(await screen.findByText(/Nothing to show yet: this phase has not called its model/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /What the worker saw/ })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "close" }));
    expect(screen.queryByRole("heading", { name: /What the worker saw/ })).toBeNull();
  });
});
