import { screen } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { installServer } from "./test/fakeServer";

// main.tsx registers its listeners when it is imported, once for the whole file, as a page load does.
let main: typeof import("./main");
let originalReload: () => void;
const reload = vi.fn();

function dispatchPreloadError(): Event {
  const event = new Event("vite:preloadError", { cancelable: true });
  window.dispatchEvent(event);
  return event;
}

describe("main", () => {
  beforeAll(async () => {
    installServer({});
    document.body.innerHTML = '<div id="root"></div>';
    main = await import("./main");
    originalReload = main.replaceReloadPage(reload);
  });

  afterAll(() => main.replaceReloadPage(originalReload));

  beforeEach(() => {
    reload.mockClear();
    sessionStorage.clear();
  });

  it("renders the page into #root", async () => {
    expect(await screen.findByText("Nothing open")).toBeInTheDocument();
  });

  it("reloads once when a newer build deleted a code file, and not a second time in the same session", () => {
    const first = dispatchPreloadError();
    expect(reload).toHaveBeenCalledTimes(1);
    expect(first.defaultPrevented).toBe(true);

    const second = dispatchPreloadError();
    expect(reload).toHaveBeenCalledTimes(1);
    expect(second.defaultPrevented).toBe(false);
  });

  it("allows a reload again 10 seconds after the reloaded page loaded", () => {
    vi.useFakeTimers();
    dispatchPreloadError();
    window.dispatchEvent(new Event("load"));
    vi.advanceTimersByTime(9_999);
    dispatchPreloadError();
    expect(reload).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(1);
    dispatchPreloadError();
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it("does not reload when session storage cannot be used: the reloaded page could not tell it reloaded, and would loop", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const event = dispatchPreloadError();
    expect(reload).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
  });

  it("does not throw from the timer when session storage cannot be cleared", () => {
    vi.useFakeTimers();
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    window.dispatchEvent(new Event("load"));
    expect(() => vi.advanceTimersByTime(10_000)).not.toThrow();
  });
});
