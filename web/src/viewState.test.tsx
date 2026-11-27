import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { readViewState, readViewValue, saveViewValue, usePersistentState } from "./viewState";

function Counter() {
  const [count, setCount] = usePersistentState("count", 0);
  return <button onClick={() => setCount(count + 1)}>count {count}</button>;
}

describe("viewState", () => {
  it("keeps values in one cookie, reads them back, and removes a value set to null", () => {
    expect(readViewState()).toEqual({});
    saveViewValue("tab", "runs");
    saveViewValue("opened", { kind: "run", root: "output", name: "AcmeSync-demo" });
    expect(document.cookie).toMatch(/^i2k-view=/);
    expect(readViewValue("tab", "inbox")).toBe("runs");
    expect(readViewValue("opened", null)).toEqual({ kind: "run", root: "output", name: "AcmeSync-demo" });
    saveViewValue("opened", null);
    expect(readViewState()).toEqual({ tab: "runs" });
    expect(readViewValue("missing", "fallback")).toBe("fallback");
  });

  it("reads a saved value an older page wrote as no value: of another kind, or not one of the values it may be", () => {
    saveViewValue("tab", "runs");
    saveViewValue("count", "five");
    expect(readViewValue("tab", "inbox", (value) => ["inbox", "queue"].includes(value as string))).toBe("inbox");
    expect(readViewValue("count", 0)).toBe(0);
  });

  it("treats a cookie it cannot read as no value", () => {
    document.cookie = "i2k-view=%7Bnot-json; path=/";
    expect(readViewState()).toEqual({});
    document.cookie = `i2k-view=${encodeURIComponent("[1,2]")}; path=/`;
    expect(readViewState()).toEqual({});
  });

  it("starts a piece of state from the cookie and saves every change", () => {
    saveViewValue("count", 4);
    render(<Counter />);
    expect(screen.getByRole("button")).toHaveTextContent("count 4");
    act(() => screen.getByRole("button").click());
    expect(readViewValue("count", 0)).toBe(5);
  });
});
