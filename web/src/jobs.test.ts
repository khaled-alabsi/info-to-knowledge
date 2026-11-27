import { describe, expect, it } from "vitest";
import { describeError } from "./api";
import type { Job, JobStatus } from "./api";
import { describeChangePill, describeQueued } from "./jobs";

describe("the message of a queue", () => {
  it("counts many queued recordings, never lists them defect", () => {
    // Defect: 32 videos queued at once listed every name, and the message covered half the page. Fix: past three
    // names the message gives their count
    const names = Array.from({ length: 32 }, (_, index) => `AcmeSync weekly ${index + 1}.mp4`);
    expect(describeQueued(names, 0)).toBe("Starting AcmeSync weekly 1.mp4: it shows as starting until its run appears; "
      + "31 recordings queued behind it");
    expect(describeQueued(names, 2)).toBe("Queued 32 recordings behind 2 jobs");
    expect(describeQueued(["Gateway.mp4", "Jonas.mp4"], 0)).toContain("Jonas.mp4 queued behind it");
  });
});

describe("a thrown value's words", () => {
  it("a thrown plain object says its text, never [object Object] defect", () => {
    // Defect: a diagram's error read "This diagram could not be drawn: [object Object]", since Mermaid throws a plain
    // object. Fix: its message, or its text, is shown
    expect(describeError({ message: "Parse error on line 2" })).toBe("Parse error on line 2");
    expect(describeError({ str: "Expecting SQE" })).toBe("Expecting SQE");
    expect(describeError({ hash: 1 })).toBe('{"hash":1}');
    expect(describeError(new Error("plain"))).toBe("plain");
    expect(describeError("words")).toBe("words");
  });
});

function buildJob(status: JobStatus, onlyPhase: number | null = 7): Job {
  return { job_id: "j", status, pid: null, exit_code: null, queued_at: "", started_at: "", ended_at: "", command: [],
    is_output_missing: false,
    request: { video: "v", output_root: "o", only_phase: onlyPhase, from_phase: null, to_phase: null, worker_indexes: [],
      is_resuming: true, is_dry_run: false } };
}

describe("a row's last state", () => {
  it("names each state of a job on a run with a good document", () => {
    expect(describeChangePill(buildJob("queued"), true)?.label).toBe("change queued");
    expect(describeChangePill(buildJob("running"), true)?.label).toBe("changing");
    expect(describeChangePill(buildJob("incomplete"), true)?.label).toBe("change failed");
    expect(describeChangePill(buildJob("crashed"), true)?.pillClass).toBe("failed");
    expect(describeChangePill(buildJob("stopped"), true)?.label).toBe("change stopped");
  });

  it("a job that ended well, was taken off the queue or whose end is unknown leaves done defect", () => {
    // Defect: a done run queued again whose new job failed still read done. Fix: its last job's state is the pill, and a
    // job that changed nothing leaves the pill it had
    expect(describeChangePill(buildJob("finished"), true)).toBeNull();
    expect(describeChangePill(buildJob("cancelled"), true)).toBeNull();
    expect(describeChangePill(buildJob("lost"), true)).toBeNull();
  });

  it("a run with no document, or no job, keeps its own words", () => {
    expect(describeChangePill(buildJob("incomplete"), false)).toBeNull();
    expect(describeChangePill(undefined, true)).toBeNull();
  });

  it("a change whose end no server saw reads the run's own record defect", () => {
    // Defect: a PDF run's knowledge pass stopped at part 16 of 21, its job ended "lost", and the run list read "done"
    // while the run's header read "failed". Fix: a change whose job ended unseen takes the run's own status
    expect(describeChangePill(buildJob("lost"), true, "failed")?.label).toBe("change failed");
    expect(describeChangePill(buildJob("lost"), true, "complete")).toBeNull();
  });

  it("a plain run still under way past its document is no change", () => {
    expect(describeChangePill(buildJob("running", null), true)).toBeNull();
    expect(describeChangePill(buildJob("incomplete", null), true)?.label).toBe("change failed");
  });
});
