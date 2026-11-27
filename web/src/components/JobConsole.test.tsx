import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FakeUpdateSource, installServer, makeJob } from "../test/fakeServer";
import JobConsole from "./JobConsole";

describe("JobConsole", () => {
  it("shows the job's run line and what its process printed, and follows the job's status", async () => {
    const onBack = vi.fn();
    const job = makeJob({}, { only_phase: 2 });
    render(<JobConsole job={job} onBack={onBack} />);
    expect(FakeUpdateSource.latest().url).toBe("/api/jobs/job-1/update?offset=0");
    // named as its Queue row names it: the run folder
    expect(screen.getByRole("heading", { level: 2, name: "AcmeSync-demo" })).toBeInTheDocument();
    expect(screen.getByText("job job-1 · phase 2")).toBeInTheDocument();
    expect(screen.getByText("run --video /work/inbox/AcmeSync-demo.mp4")).toBeInTheDocument();
    expect(screen.getByText("queued")).toHaveClass("pill");
    expect(screen.getByText("The job has printed nothing yet.")).toBeInTheDocument();

    act(() => FakeUpdateSource.latest().emit({ job: { ...job, status: "refused", exit_code: 2 },
      text: "refused: --phase 2 needs 01_transcript.json\n", offset: 44 }));
    expect(screen.getByText("refused")).toHaveClass("pill", "refused");
    expect(screen.getByText("refused")).toHaveAttribute("title", expect.stringContaining("exit code 2"));
    expect(screen.getByText("refused: --phase 2 needs 01_transcript.json")).toBeInTheDocument();

    act(() => FakeUpdateSource.latest().emit({ job: null, text: "", offset: 44 }));
    expect(screen.getByText("queued")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "back" }));
    expect(onBack).toHaveBeenCalled();
  });

  it("opens the run the job writes, and takes a waiting job off the queue or interrupts a running one", async () => {
    const calls = installServer({ "POST /api/jobs/job-1/cancel": makeJob({ status: "cancelled" }),
      "POST /api/jobs/job-2/interrupt": { interrupted: true } });
    const onOpenRun = vi.fn();
    const { rerender } = render(<JobConsole job={makeJob()} onBack={() => {}} onOpenRun={onOpenRun} onChanged={() => {}}
      onMessage={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Open run" }));
    expect(onOpenRun).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Take off the queue" }));
    expect(calls.some((call) => call.url === "/api/jobs/job-1/cancel")).toBe(true);
    rerender(<JobConsole job={makeJob({ job_id: "job-2", status: "running" })} onBack={() => {}} onOpenRun={onOpenRun}
      onChanged={() => {}} onMessage={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "Interrupt" }));
    expect(calls.some((call) => call.url === "/api/jobs/job-2/interrupt")).toBe(true);
  });
});
