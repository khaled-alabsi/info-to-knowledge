import { describe, expect, it, vi } from "vitest";
import { NO_SELECTION, buildFileUrl, describePdfChoice, describePdfFlow, describePdfMode, fetchJson, formatDuration, formatTime, queueJobs, sendJson, uploadFiles } from "./api";
import { FakeXMLHttpRequest, answerJson, installServer, makeJob } from "./test/fakeServer";

describe("describePdfFlow", () => {
  it("names the flow each PDF mode takes, the markdown conversion its own", () => {
    expect(describePdfFlow("markdown-conversion")).toBe("pdf-markdown-conversion");
    expect(describePdfFlow("pages-as-images")).toBe("pdf-pages");
    expect(describePdfFlow("")).toBe("pdf-text-only");
    expect(describePdfMode("markdown-conversion").label).toBe("markdown conversion");
  });
});

describe("describePdfChoice", () => {
  it("names the LLM pass a PDF's document was given in its Document pill defect", () => {
    // Defect: a markdown conversion given a knowledge pass showed "Document: Markdown", which said nothing of the pass
    // Fix: the pill names the shaping as the run form lists it, the pass with it
    expect(describePdfChoice("markdown-conversion", "knowledge").document).toBe("Markdown + LLM knowledge reshaping");
    // the knowledge profile is named by what it gives, never "knowledge" beside the knowledge reshaping
    expect(describePdfChoice("text-only", "").document).toBe("LLM-written document");
    expect(describePdfChoice("markdown-conversion", "cleanup").document).toBe("Markdown + LLM clean up");
    expect(describePdfChoice("markdown-conversion", "").document).toBe("Markdown");
    expect(describePdfChoice("text-only", "rewrite").document).toBe("LLM-written document, then rewrite with LLM");
    expect(describePdfChoice("pages-as-images", "")).toEqual({ processing: "Pages as images",
      document: "LLM-written document" });
  });
});

describe("formatTime", () => {
  it("writes a moment of the recording as a player shows it", () => {
    expect(formatTime(0)).toBe("0:00");
    expect(formatTime(419.8)).toBe("6:59");
    expect(formatTime(4019)).toBe("1:06:59");
    expect(formatTime(-5)).toBe("0:00");
  });
});

describe("formatDuration", () => {
  it("writes how long something took", () => {
    expect(formatDuration(45.4)).toBe("45s");
    expect(formatDuration(192)).toBe("3m 12s");
    expect(formatDuration(3840)).toBe("1h 04m");
    expect(formatDuration(-3)).toBe("0s");
  });
});

describe("buildFileUrl", () => {
  it("encodes each part of the path and keeps the slashes", () => {
    expect(buildFileUrl("/runs/knowledge/AcmeSync", "Gateway notes/figure #1.png"))
      .toBe("/api/runs/knowledge/AcmeSync/files/Gateway%20notes/figure%20%231.png");
  });
});

describe("fetchJson and sendJson", () => {
  it("names each problem of a request the server could not validate, never [object Object]", async () => {
    installServer({ "POST /api/jobs": answerJson({ detail: [{ loc: ["body", "items"], msg: "Field required", type: "missing" }] }, 422) });
    await expect(sendJson("/jobs", {})).rejects.toThrow("422: Field required");
  });

  it("returns the parsed answer and posts JSON", async () => {
    const calls = installServer({ "GET /api/inbox": [], "POST /api/jobs": [makeJob()] });
    await expect(fetchJson("/inbox")).resolves.toEqual([]);
    const jobs = await queueJobs([{ video: "/work/inbox/a.mp4", output: "/work/knowledge/a" }], NO_SELECTION);
    expect(jobs[0].job_id).toBe("job-1");
    expect(calls[1]).toEqual({ method: "POST", url: "/api/jobs",
      body: { items: [{ video: "/work/inbox/a.mp4", output: "/work/knowledge/a" }], ...NO_SELECTION } });
  });

  it("names the server's detail in the error, or the status text when the body is no JSON", async () => {
    installServer({
      "POST /api/jobs/job-9/cancel": answerJson({ detail: "unknown job job-9" }, 404),
      "GET /api/runs": new Response("<html>", { status: 502, statusText: "Bad Gateway" }),
    });
    await expect(sendJson("/jobs/job-9/cancel")).rejects.toThrow("404: unknown job job-9");
    await expect(fetchJson("/runs")).rejects.toThrow("502: Bad Gateway");
  });
});

describe("uploadFiles", () => {
  const recording = new File(["x"], "AcmeSync.mp4", { type: "video/mp4" });

  it("reports progress and resolves with what the server saved", async () => {
    const onProgress = vi.fn();
    const outcome = uploadFiles([{ file: recording, path: recording.name }], onProgress);
    const request = FakeXMLHttpRequest.latest();
    expect(request.method).toBe("POST");
    expect(request.url).toBe("/api/inbox/upload");
    expect((request.sent?.get("files") as File).name).toBe("AcmeSync.mp4");
    request.reportProgress(25, 100);
    request.reportProgress(50, 0, false);
    expect(onProgress.mock.calls).toEqual([[0.25]]);
    request.respond(200, [{ name: "AcmeSync.mp4", saved_as: "AcmeSync-2.mp4", error: "" }]);
    await expect(outcome).resolves.toEqual([{ name: "AcmeSync.mp4", saved_as: "AcmeSync-2.mp4", error: "" }]);
  });

  it("rejects with the status on a refused upload and says when the server was not reached", async () => {
    const refused = uploadFiles([{ file: recording, path: recording.name }], () => {});
    FakeXMLHttpRequest.latest().respond(413, "too large");
    await expect(refused).rejects.toThrow("413: too large");
    const lost = uploadFiles([{ file: recording, path: recording.name }], () => {});
    FakeXMLHttpRequest.latest().failToConnect();
    await expect(lost).rejects.toThrow("the upload did not reach the server");
  });
});
