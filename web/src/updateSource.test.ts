import { afterEach, describe, expect, it, vi } from "vitest";
import { answerJson, installServer } from "./test/fakeServer";
import { PolledUpdateSource, UPDATE_INTERVAL_MS } from "./updateSource";

describe("PolledUpdateSource", () => {
  afterEach(() => vi.useRealTimers());

  it("asks once, hands the answer on, and asks again a second later from the offset it gave, closing each request", async () => {
    vi.useFakeTimers();
    const calls = installServer({ "GET /api/jobs/job-1/update?offset=0": { offset: 12, text: "phase 1\n" },
      "GET /api/jobs/job-1/update?offset=12": { offset: 12, text: "" } });
    // the reader keeps the offset; each ask is built from it
    let offset = 0;
    const source = new PolledUpdateSource(() => `/api/jobs/job-1/update?offset=${offset}`);
    const seen: unknown[] = [];
    source.onupdate = (update) => {
      seen.push(update);
      offset = update.offset;
    };
    await vi.advanceTimersByTimeAsync(0);
    expect(seen).toEqual([{ offset: 12, text: "phase 1\n" }]);
    await vi.advanceTimersByTimeAsync(UPDATE_INTERVAL_MS);
    expect(calls.map((call) => call.url)).toEqual(["/api/jobs/job-1/update?offset=0", "/api/jobs/job-1/update?offset=12"]);
    source.close();
    await vi.advanceTimersByTimeAsync(UPDATE_INTERVAL_MS * 3);
    expect(calls).toHaveLength(2);
  });

  it("says a failed ask through onerror and asks no more", async () => {
    vi.useFakeTimers();
    const calls = installServer({ "GET /api/jobs/gone/update?offset=0": answerJson({ detail: "no job" }, 404) });
    const source = new PolledUpdateSource(() => "/api/jobs/gone/update?offset=0");
    const onError = vi.fn();
    source.onerror = onError;
    await vi.advanceTimersByTimeAsync(UPDATE_INTERVAL_MS * 3);
    expect(onError).toHaveBeenCalledTimes(1);
    expect(calls).toHaveLength(1);
  });
});
