import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import LogView from "./LogView";

// A 1000 px log in a 200 px box: the end is at scrollTop 800.
function sizeLog(box: HTMLElement) {
  Object.defineProperty(box, "scrollHeight", { configurable: true, value: 1000 });
  Object.defineProperty(box, "clientHeight", { configurable: true, value: 200 });
}

describe("LogView", () => {
  it("says on the button when the browser refuses to copy", async () => {
    vi.stubGlobal("navigator", Object.create(navigator, {
      clipboard: { value: { writeText: vi.fn(async () => { throw new Error("not allowed"); }) } } }));
    render(<LogView title="Log" text="line 1" empty="" />);
    await userEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByRole("button", { name: "Copy failed" })).toBeInTheDocument();
  });

  it("says when the log is empty, and Copy is off", () => {
    render(<LogView title="Job console" text="" empty="The job has printed nothing yet." />);
    expect(screen.getByText("The job has printed nothing yet.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy" })).toBeDisabled();
  });

  it("follows the end while the reader is there, stays put after a scroll up, and Follow jumps back", () => {
    const { rerender } = render(<LogView title="Log" text="line 1" empty="" />);
    const box = document.querySelector<HTMLElement>("pre.log")!;
    sizeLog(box);
    rerender(<LogView title="Log" text={"line 1\nline 2"} empty="" />);
    expect(box.scrollTop).toBe(1000);

    box.scrollTop = 300;
    fireEvent.scroll(box);
    expect(screen.getByRole("button", { name: "Follow" })).not.toHaveClass("active");
    rerender(<LogView title="Log" text={"line 1\nline 2\nline 3"} empty="" />);
    expect(box.scrollTop).toBe(300);

    fireEvent.click(screen.getByRole("button", { name: "Follow" }));
    expect(box.scrollTop).toBe(1000);
    box.scrollTop = 790;
    fireEvent.scroll(box);
    expect(screen.getByRole("button", { name: "Follow" })).toHaveClass("active");
  });

  it("Wrap starts off, and turns line wrapping on and off", async () => {
    render(<LogView title="Log" text="AcmeSync" empty="" />);
    const box = document.querySelector("pre.log")!;
    expect(box).not.toHaveClass("wrap");
    await userEvent.click(screen.getByRole("button", { name: "Wrap" }));
    expect(box).toHaveClass("wrap");
    await userEvent.click(screen.getByRole("button", { name: "Wrap" }));
    expect(box).not.toHaveClass("wrap");
  });

  it("keeps the Wrap choice after the page reloads defect", async () => {
    // Defect: the owner turned Wrap on or off, and every reload or other log started wrapped again.
    // Fix: the choice is kept in the page's cookie like its other settings, and a first visit starts unwrapped.
    for (const clicks of [1, 2]) {
      document.cookie = "i2k-view=; path=/; max-age=0";
      const first = render(<LogView title="Log" text="AcmeSync" empty="" />);
      for (let i = 0; i < clicks; i++) await userEvent.click(screen.getByRole("button", { name: "Wrap" }));
      const chosen = screen.getByRole("button", { name: "Wrap" }).getAttribute("aria-pressed");
      first.unmount();
      render(<LogView title="Other log" text="BetaSync" empty="" />);
      expect(screen.getByRole("button", { name: "Wrap" })).toHaveAttribute("aria-pressed", chosen);
      expect(document.querySelector("pre.log")!.classList.contains("wrap")).toBe(chosen === "true");
      cleanup();
    }
  });

  it("Copy copies the whole log and says Copied for 1.5 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<LogView title="Log" text={"Gateway up\nJonas joined"} empty="" />);
    await user.click(screen.getByRole("button", { name: "Copy" }));
    expect(await navigator.clipboard.readText()).toBe("Gateway up\nJonas joined");
    expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1500));
    expect(screen.getByRole("button", { name: "Copy" })).toBeInTheDocument();
  });
});
