import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Dropdown, Menu } from "./Dropdown";

type Box = { left: number; top: number; width: number; height: number };

// Places the button at `anchor` and the panel at `panel` size, in a 1000 x 800 window.
function placeOnScreen(anchor: Box, panel: Box) {
  vi.spyOn(window, "innerWidth", "get").mockReturnValue(1000);
  vi.spyOn(window, "innerHeight", "get").mockReturnValue(800);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function measure(this: HTMLElement) {
    const box = this.classList.contains("dropdown-panel") ? panel : anchor;
    return { ...box, x: box.left, y: box.top, right: box.left + box.width, bottom: box.top + box.height, toJSON: () => ({}) };
  });
}

function readPanel(): HTMLElement | null {
  return document.querySelector<HTMLElement>(".dropdown-panel");
}

describe("Dropdown", () => {
  it("opens its panel on the page's top layer under the button, and closes it from inside", async () => {
    placeOnScreen({ left: 100, top: 50, width: 80, height: 30 }, { left: 0, top: 0, width: 200, height: 100 });
    const { container } = render(<div className="scroller"><Dropdown label="Run" icon="layers">
      {(close) => <button onClick={close}>Queue this run</button>}
    </Dropdown></div>);
    const button = screen.getByRole("button", { name: "Run" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(button);
    expect(button).toHaveAttribute("aria-expanded", "true");
    expect(button).toHaveClass("active");
    expect(readPanel()?.parentElement).toBe(document.body);
    expect(container.querySelector(".dropdown-panel")).toBeNull();
    expect(readPanel()).toHaveStyle({ top: "86px", left: "100px", visibility: "visible" });
    await userEvent.click(screen.getByRole("button", { name: "Queue this run" }));
    expect(readPanel()).toBeNull();
  });

  it("opens above the button when the space below runs out, right-aligned, and kept 12 px inside the window", async () => {
    placeOnScreen({ left: 950, top: 700, width: 40, height: 30 }, { left: 0, top: 0, width: 300, height: 200 });
    render(<Dropdown icon="more" align="right" title="more actions">{() => <p>panel</p>}</Dropdown>);
    await userEvent.click(screen.getByTitle("more actions"));
    expect(readPanel()).toHaveStyle({ top: "494px", left: "688px" });
  });

  it("measures again when the window is resized", async () => {
    placeOnScreen({ left: 100, top: 50, width: 80, height: 30 }, { left: 0, top: 0, width: 200, height: 100 });
    render(<Dropdown label="Run">{() => <p>panel</p>}</Dropdown>);
    await userEvent.click(screen.getByRole("button", { name: "Run" }));
    placeOnScreen({ left: 300, top: 50, width: 80, height: 30 }, { left: 0, top: 0, width: 200, height: 100 });
    act(() => {
      window.dispatchEvent(new Event("resize"));
    });
    expect(readPanel()).toHaveStyle({ left: "300px" });
  });

  it("closes on a click outside, on Escape and on a page scroll, but not on a click or scroll inside", async () => {
    render(<><Dropdown label="Run">{() => <p>inside</p>}</Dropdown><p>outside</p></>);
    const button = screen.getByRole("button", { name: "Run" });
    await userEvent.click(button);
    fireEvent.mouseDown(screen.getByText("inside"));
    fireEvent.scroll(readPanel()!);
    fireEvent.keyDown(document, { key: "Enter" });
    expect(readPanel()).not.toBeNull();
    fireEvent.mouseDown(screen.getByText("outside"));
    expect(readPanel()).toBeNull();

    await userEvent.click(button);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(readPanel()).toBeNull();

    await userEvent.click(button);
    fireEvent.scroll(document);
    expect(readPanel()).toBeNull();

    await userEvent.click(button);
    await userEvent.click(button);
    expect(readPanel()).toBeNull();
  });
});

describe("Menu", () => {
  it("lists its actions with their hints, runs the chosen one and closes; a disabled one does nothing", async () => {
    const onPause = vi.fn();
    const onStop = vi.fn();
    render(<Menu label="Control" items={[
      { label: "Pause", hint: "hold at the next check", icon: "pause", onSelect: onPause },
      { label: "Stop", isDanger: true, isDisabled: true, onSelect: onStop },
    ]} />);
    await userEvent.click(screen.getByRole("button", { name: "Control" }));
    expect(screen.getByRole("menuitem", { name: /Pause/ })).toHaveTextContent("Pausehold at the next check");
    const stop = screen.getByRole("menuitem", { name: "Stop" });
    expect(stop).toHaveClass("danger");
    // a disabled item stays reachable, so its reason is read; choosing it does nothing
    expect(stop).toHaveAttribute("aria-disabled", "true");
    await userEvent.click(stop);
    expect(onStop).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("menuitem", { name: /Pause/ }));
    expect(onPause).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("shows its note above the actions, in place of a hint repeated under each", async () => {
    render(<Menu label="Convert" note="What every action here does" items={[{ label: "To one", onSelect: vi.fn() }]} />);
    await userEvent.click(screen.getByRole("button", { name: "Convert" }));
    expect(screen.getByText("What every action here does").closest(".menu-note")).not.toBeNull();
    expect(screen.getByRole("menuitem", { name: "To one" })).toHaveTextContent(/^To one$/);
    expect(screen.getAllByRole("menuitem")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "explain this menu" })).not.toBeInTheDocument();
  });

  it("opens its help under the note, inside the menu, from the note's own button", async () => {
    render(<Menu label="Convert" note="What every action here does" help={<p>Each one makes a new run folder.</p>}
      items={[{ label: "To one", onSelect: vi.fn() }]} />);
    await userEvent.click(screen.getByRole("button", { name: "Convert" }));
    expect(screen.queryByText("Each one makes a new run folder.")).not.toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("menu")).getByRole("button", { name: "explain this menu" }));
    expect(within(screen.getByRole("menu")).getByText("Each one makes a new run folder.")).toBeInTheDocument();
  });
});
