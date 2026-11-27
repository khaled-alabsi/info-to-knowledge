import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FakeUpdateSource } from "./test/fakeServer";
import { useUpdateStream } from "./useUpdateStream";

describe("useUpdateStream", () => {
  it("opens no stream without a path", () => {
    const { result } = renderHook(() => useUpdateStream(null));
    expect(FakeUpdateSource.instances).toHaveLength(0);
    expect(result.current).toEqual({ latest: null, text: "" });
  });

  it("reconnects from the last offset after a lost stream, without repeating the log", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useUpdateStream<{ offset: number; text: string }>("/runs/knowledge/AcmeSync/update"));
    const first = FakeUpdateSource.latest();
    expect(first.url).toBe("/api/runs/knowledge/AcmeSync/update?offset=0");
    act(() => first.emit({ offset: 12, text: "phase 1 done\n" }));
    act(() => first.emit({ offset: 12, text: "" }));
    act(() => first.fail());
    expect(first.isClosed).toBe(true);
    expect(FakeUpdateSource.instances).toHaveLength(1);

    act(() => vi.advanceTimersByTime(2000));
    const second = FakeUpdateSource.latest();
    expect(second).not.toBe(first);
    expect(second.url).toBe("/api/runs/knowledge/AcmeSync/update?offset=12");
    act(() => second.emit({ offset: 25, text: "phase 2 done\n" }));
    expect(result.current.text).toBe("phase 1 done\nphase 2 done\n");
    expect(result.current.latest).toEqual({ offset: 25, text: "phase 2 done\n" });
  });

  it("stops asking once the server says the run or job is gone", () => {
    vi.useFakeTimers();
    renderHook(() => useUpdateStream("/runs/knowledge/Gone/update"));
    act(() => FakeUpdateSource.latest().fail(404));
    act(() => vi.advanceTimersByTime(10_000));
    expect(FakeUpdateSource.instances).toHaveLength(1);
  });

  it("starts the text again when the offset goes back (a newer log replaced the old one)", () => {
    const { result } = renderHook(() => useUpdateStream("/jobs/job-1/update?tail=1"));
    const source = FakeUpdateSource.latest();
    expect(source.url).toBe("/api/jobs/job-1/update?tail=1&offset=0");
    act(() => source.emit({ offset: 40, text: "old log" }));
    act(() => source.emit({ offset: 6, text: "new log" }));
    expect(result.current.text).toBe("new log");
  });

  it("keeps only the last 400,000 characters of a log", () => {
    const { result } = renderHook(() => useUpdateStream("/jobs/job-1/update"));
    act(() => FakeUpdateSource.latest().emit({ offset: 1, text: "a".repeat(399_999) }));
    act(() => FakeUpdateSource.latest().emit({ offset: 2, text: "bc" }));
    expect(result.current.text).toHaveLength(400_000);
    expect(result.current.text.endsWith("abc")).toBe(true);
  });

  it("closes the stream and cancels a pending reconnect on unmount, and starts over on a new path", () => {
    vi.useFakeTimers();
    const { result, rerender, unmount } = renderHook(({ path }) => useUpdateStream(path), { initialProps: { path: "/jobs/a/update" } });
    act(() => FakeUpdateSource.latest().emit({ offset: 3, text: "abc" }));
    rerender({ path: "/jobs/b/update" });
    expect(FakeUpdateSource.instances[0].isClosed).toBe(true);
    expect(result.current.text).toBe("");
    expect(FakeUpdateSource.latest().url).toBe("/api/jobs/b/update?offset=0");
    act(() => FakeUpdateSource.latest().fail());
    unmount();
    act(() => vi.advanceTimersByTime(5000));
    expect(FakeUpdateSource.instances).toHaveLength(2);
  });
});
