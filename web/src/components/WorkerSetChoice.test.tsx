import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NO_SELECTION } from "../api";
import type { WorkerSets } from "../api";
import { WorkerSetChoice, describeWorkerSet } from "./WorkerSetChoice";

const SETS: WorkerSets = { default_set: "default", sets: [
  { name: "default", changes: [] }, { name: "Mac", changes: ["lmstudioBaseUrl"] }, { name: "gpu-box", changes: ["models"] }] };

describe("WorkerSetChoice", () => {
  it("shows no choice while the sets are unknown or the settings define only the default", () => {
    const { container, rerender } = render(<WorkerSetChoice sets={null} selection={NO_SELECTION} onChange={() => {}} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<WorkerSetChoice sets={{ default_set: "default", sets: [SETS.sets[0]] }} selection={NO_SELECTION} onChange={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists the default first, then each set, and puts the picked name into the selection", async () => {
    const onChange = vi.fn();
    render(<WorkerSetChoice sets={SETS} selection={NO_SELECTION} onChange={onChange} />);
    const buttons = screen.getAllByRole("radio");
    expect(buttons.map((button) => button.textContent)).toEqual(["default (default)", "Mac", "gpu-box"]);
    expect(buttons[0]).toHaveAttribute("aria-checked", "true");
    await userEvent.click(screen.getByRole("radio", { name: "gpu-box" }));
    expect(onChange).toHaveBeenCalledWith({ ...NO_SELECTION, worker_set: "gpu-box" });
  });

  it("picking the default again sends an empty name, and the chosen set is named in words", async () => {
    const onChange = vi.fn();
    const chosen = { ...NO_SELECTION, worker_set: "Mac" };
    render(<WorkerSetChoice sets={SETS} selection={chosen} onChange={onChange} />);
    expect(screen.getByRole("radio", { name: "Mac" })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(screen.getByRole("radio", { name: "default (default)" }));
    expect(onChange).toHaveBeenCalledWith({ ...chosen, worker_set: "" });
    expect(describeWorkerSet(chosen, SETS)).toBe("Mac");
    expect(describeWorkerSet(NO_SELECTION, SETS)).toBe("default (default)");
  });
});
