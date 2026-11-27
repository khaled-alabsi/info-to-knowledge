import { afterEach, describe, expect, it, vi } from "vitest";
import { copyText } from "./clipboard";

// A navigator with no clipboard, as a browser gives a page opened over plain http at a network address
const withoutClipboard = () => {
  const { clipboard: _clipboard, ...rest } = Object.getOwnPropertyDescriptors(Navigator.prototype);
  vi.stubGlobal("navigator", Object.create(Object.prototype, rest));
};

describe("copyText", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("copies on a page opened at the machine's network address over plain http defect", async () => {
    // Defect: Copy showed "Copy failed" on the page opened at http://10.0.0…, where the browser offers no clipboard.
    // Fix: there the text goes through a hidden text box and the browser's own copy command.
    withoutClipboard();
    let copied = "";
    document.execCommand = vi.fn(() => {
      copied = document.querySelector("textarea")?.value ?? "";
      return true;
    });
    await copyText("frame-012 blocked");
    expect(document.execCommand).toHaveBeenCalledWith("copy");
    expect(copied).toBe("frame-012 blocked");
    expect(document.querySelector("textarea")).toBeNull();
  });

  it("falls back to the copy command when the clipboard refuses", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: vi.fn(() => Promise.reject(new Error("denied"))) } });
    document.execCommand = vi.fn(() => true);
    await copyText("text");
    expect(document.execCommand).toHaveBeenCalledWith("copy");
  });

  it("says it failed when the browser refuses the copy command too", async () => {
    withoutClipboard();
    document.execCommand = vi.fn(() => false);
    await expect(copyText("text")).rejects.toThrow("refused");
  });

  it("uses the clipboard when the browser offers one", async () => {
    const writeText = vi.fn(() => Promise.resolve());
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    document.execCommand = vi.fn(() => true);
    await copyText("text");
    expect(writeText).toHaveBeenCalledWith("text");
    expect(document.execCommand).not.toHaveBeenCalled();
  });
});
