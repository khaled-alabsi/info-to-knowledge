import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Job } from "../api";
import { answerJson, installServer, makeJob } from "../test/fakeServer";
import { describeJob } from "../jobs";
import QueuePanel from "./QueuePanel";

const JOBS: Job[] = [
  makeJob({ job_id: "job-1", status: "finished", exit_code: 0, ended_at: "2026-10-01T13:05:00" }),
  makeJob({ job_id: "job-2", status: "running" }, { video: "/work/inbox/Jonas-call.mp4", output_root: "/work/output/Jonas-call" }),
  makeJob({ job_id: "job-3" }, { video: "/work/inbox/Gateway.mp4", output_root: "/work/output/Gateway", from_phase: 4 }),
  makeJob({ job_id: "job-4" }, { video: "/work/inbox/Second.mp4", output_root: "/work/output/Second" }),
  makeJob({ job_id: "job-5", status: "refused", exit_code: 2, ended_at: "2026-10-01T14:10:00" }),
  makeJob({ job_id: "job-6", status: "cancelled" }),
];

function renderQueue(jobs = JOBS, openJobId = "") {
  const onOpen = vi.fn();
  const onChanged = vi.fn();
  const onMessage = vi.fn();
  render(<QueuePanel jobs={jobs} openJobId={openJobId} onOpen={onOpen} onChanged={onChanged} onMessage={onMessage} />);
  return { onOpen, onChanged, onMessage };
}

function listWaiting(): HTMLElement {
  return document.querySelector<HTMLElement>(".tab-body > ul.rows")!;
}

// the waiting rows: the ones numbered in queue order (a running job's row stands above them, unnumbered)
function listWaitingRows(): HTMLElement[] {
  return within(listWaiting()).getAllByRole("listitem").filter((row) => row.querySelector(".position"));
}

describe("describeJob", () => {
  it("names every part of the request, or every phase", () => {
    expect(describeJob(makeJob())).toBe("every phase");
    expect(describeJob(makeJob({}, { is_resuming: true, only_phase: 3, from_phase: 2, to_phase: 4, worker_indexes: [12, 40], is_dry_run: true })))
      .toBe("resume · phase 3 · from 2 · to 4 · frames 12, 40 · dry run");
    expect(describeJob(makeJob({}, { from_phase: 4, purpose: "convert to knowledge · phase 4 only (document writing)" })))
      .toBe("convert to knowledge · phase 4 only (document writing)");
  });

  it("names the settings and the workflow a job runs with, and says it is loading before the jobs arrive defect", () => {
    // Defect: 32 videos queued with the fast settings and the Fast workflow each read "every phase" alone, and the queue
    // said "Nothing is waiting" before it had read the jobs. Fix: the job names both; the queue says it is loading
    expect(describeJob(makeJob({}, { profile: "fast", workflow: "fast-processing" }))).toBe("every phase · profile fast · Fast");
    render(<QueuePanel jobs={[]} isLoaded={false} openJobId="" onOpen={() => {}} onChanged={() => {}} onMessage={() => {}} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading the queue…");
    expect(screen.queryByText(/Nothing is waiting/)).not.toBeInTheDocument();
  });
});

describe("QueuePanel", () => {
  it("numbers the waiting jobs in queue order, the running one above them unnumbered", () => {
    renderQueue();
    const rows = listWaitingRows();
    expect(rows.map((row) => row.querySelector(".position")?.textContent)).toEqual(["1", "2"]);
    expect(rows.map((row) => row.querySelector(".row-main .row-name")?.textContent)).toEqual(["Gateway", "Second"]);
    expect(within(rows[0]).getByText("from 4 · 10-01 12:30")).toBeInTheDocument();
    expect(within(listWaiting()).getAllByRole("listitem")[0]).toHaveTextContent("Jonas-call");
  });

  it("groups the running and waiting jobs by worker set when more than one set has jobs, each numbered in its set defect", () => {
    // Defect: with jobs on three worker sets, the queue showed one list numbered 1 to 20, so the owner could not see
    // which set ran what or what each set ran next. Fix: one group per set, its running job first, its waiting jobs
    // numbered in the order that set runs them.
    renderQueue([
      makeJob({ job_id: "gpu-run", status: "running", lane: "gpu-box" }, { video: "/work/inbox/A.mp4", output_root: "/work/output/A" }),
      makeJob({ job_id: "mac-1", lane: "Mac" }, { video: "/work/inbox/B.mp4", output_root: "/work/output/B" }),
      makeJob({ job_id: "gpu-1", lane: "gpu-box" }, { video: "/work/inbox/C.mp4", output_root: "/work/output/C" }),
      makeJob({ job_id: "mac-2", lane: "Mac" }, { video: "/work/inbox/D.mp4", output_root: "/work/output/D" }),
    ]);
    const pc = screen.getByRole("region", { name: "worker set gpu-box" });
    const mac = screen.getByRole("region", { name: "worker set Mac" });
    expect(within(pc).getByRole("heading")).toHaveTextContent("Worker set gpu-box · 1 running · 1 waiting");
    expect(within(mac).getByRole("heading")).toHaveTextContent("Worker set Mac · 0 running · 2 waiting");
    expect(within(pc).getAllByRole("listitem").map((row) => row.querySelector(".row-name")?.textContent)).toEqual(["A", "C"]);
    const macRows = within(mac).getAllByRole("listitem");
    expect(macRows.map((row) => row.querySelector(".position")?.textContent)).toEqual(["1", "2"]);
    expect(within(macRows[1]).getByRole("button", { name: "run later" })).toBeDisabled();
  });

  it("shows one plain list when every job runs on one worker set", () => {
    renderQueue();
    expect(screen.queryByRole("region", { name: /worker set/ })).not.toBeInTheDocument();
  });

  it("names a converted run by its new folder, and the recording it is written from under it", () => {
    renderQueue([makeJob({ job_id: "job-7" },
      { video: "/work/output/Gateway/recording/Gateway.wav", output_root: "/work/output/Gateway-knowledge", from_phase: 4 })]);
    const row = within(listWaiting()).getByRole("listitem");
    expect(row.querySelector(".row-main .row-name")?.textContent).toBe("Gateway-knowledge");
    expect(within(row).getByText("Gateway.wav · from 4 · 10-01 12:30")).toBeInTheDocument();
  });

  it("says when nothing waits", () => {
    renderQueue([makeJob({ status: "running" })]);
    expect(screen.getByText("Nothing is waiting. A job runs as soon as nothing else runs.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Finished jobs/ })).not.toBeInTheDocument();
  });

  it("keeps ended jobs in Finished jobs, newest first, with their status and exit code", async () => {
    const { onOpen } = renderQueue(JOBS, "job-5");
    const head = screen.getByRole("button", { name: /Finished jobs\s*3/ });
    expect(head).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(head);
    const finished = [...document.querySelectorAll(".inbox-section .row")];
    expect(finished.map((row) => row.querySelector(".pill")?.textContent)).toEqual(["cancelled", "refused", "finished"]);
    expect(finished[1].querySelector(".pill")).toHaveAttribute("title", expect.stringContaining("exit code 2"));
    expect(finished[1]).toHaveClass("selected");
    expect(finished[1]).toHaveTextContent("10-01 14:10");
    expect(document.querySelectorAll(".inbox-section [aria-label=cancel]")).toHaveLength(0);
    await userEvent.click(finished[1] as HTMLElement);
    expect(onOpen).toHaveBeenCalledWith(JOBS[4]);
    await userEvent.click(head);
    expect(document.querySelectorAll(".inbox-section .row")).toHaveLength(0);
  });

  it("cancels a waiting job and says what became of it, or why not", async () => {
    let answer: object = makeJob({ job_id: "job-3", status: "cancelled" });
    const calls = installServer({ "POST /api/jobs/job-3/cancel": () => answer });
    const { onMessage, onChanged } = renderQueue();
    const first = listWaitingRows()[0];
    await userEvent.click(within(first).getByRole("button", { name: "cancel" }));
    expect(calls.at(-1)).toEqual({ method: "POST", url: "/api/jobs/job-3/cancel", body: {} });
    expect(onMessage).toHaveBeenLastCalledWith("Job job-3: taken off the queue");
    expect(onChanged).toHaveBeenCalled();
    answer = answerJson({ detail: "job job-3 is not queued" }, 409);
    await userEvent.click(within(first).getByRole("button", { name: "cancel" }));
    expect(onMessage).toHaveBeenLastCalledWith("409: job job-3 is not queued", true);
  });

  it("lists the running job above the waiting ones, opening its console", async () => {
    const { onOpen } = renderQueue();
    const running = screen.getByRole("button", { name: /Jonas-call/ });
    expect(running.querySelector(".pill")).toHaveTextContent("running");
    await userEvent.click(running);
    expect(onOpen).toHaveBeenCalledWith(JOBS[1]);
  });

  it("queues a cancelled, lost or crashed job again with its own choices", async () => {
    const calls = installServer({ "POST /api/jobs": [makeJob({ job_id: "job-9" })] });
    const lost = makeJob({ job_id: "job-7", status: "lost", command: ["python", "main.py", "run", "--profile", "strict"] },
      { video: "/work/inbox/Gateway.mp4", output_root: "/work/output/Gateway", from_phase: 3, profile: "strict" });
    const { onChanged } = renderQueue([lost]);
    await userEvent.click(screen.getByRole("button", { name: /Finished jobs/ }));
    await userEvent.click(screen.getByRole("button", { name: "queue again" }));
    expect(calls.find((call) => call.url === "/api/jobs")?.body).toMatchObject({
      items: [{ video: "/work/inbox/Gateway.mp4", output: "/work/output/Gateway" }], from_phase: 3, profile: "strict" });
    expect(onChanged).toHaveBeenCalled();
  });

  it("queues a job again with every choice it was queued with, and its own profile, not the server's", async () => {
    const calls = installServer({ "POST /api/jobs": [makeJob({ job_id: "job-9" })] });
    const cancelled = makeJob({ job_id: "job-8", status: "cancelled", command: ["python", "main.py", "run", "--profile", "strict"] },
      { video: "/work/inbox/Weekly.mp4", output_root: "/work/output/Weekly", profile: "", tags: ["demo"], ingesting_profile: "meeting",
        is_run_as_audio: true, pdf_mode: "", workflow: "fast-processing", phase_models: ["frames_processing=acme-small"] });
    renderQueue([cancelled]);
    await userEvent.click(screen.getByRole("button", { name: /Finished jobs/ }));
    await userEvent.click(screen.getByRole("button", { name: "queue again" }));
    expect(calls.find((call) => call.url === "/api/jobs")?.body).toMatchObject({ profile: "", tags: ["demo"],
      ingesting_profile: "meeting", is_run_as_audio: true, workflow: "fast-processing", phase_models: { frames_processing: "acme-small" } });
  });

  it("moves a waiting job up or down, and clears the ended jobs", async () => {
    const calls = installServer({ "POST /api/jobs/job-4/move": makeJob({ job_id: "job-4" }), "POST /api/jobs/clear": { cleared: 2 } });
    const { onChanged, onMessage } = renderQueue();
    await userEvent.click(within(listWaitingRows()[1]).getByRole("button", { name: "run sooner" }));
    expect(calls.find((call) => call.url === "/api/jobs/job-4/move")?.body).toEqual({ direction: "up" });
    expect(within(listWaitingRows()[0]).getByRole("button", { name: "run sooner" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /Finished jobs/ }));
    await userEvent.click(screen.getByRole("button", { name: "Clear ended jobs" }));
    expect(calls.some((call) => call.url === "/api/jobs/clear")).toBe(true);
    expect(onMessage).toHaveBeenLastCalledWith("2 ended jobs cleared");
    expect(onChanged).toHaveBeenCalled();
  });
});
