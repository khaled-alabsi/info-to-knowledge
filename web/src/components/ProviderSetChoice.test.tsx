import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NO_SELECTION } from "../api";
import type { ProviderSets } from "../api";
import { ProviderSetChoice, describeProviderSet } from "./ProviderSetChoice";

const SETS: ProviderSets = { default_set: "default", sets: [
  { name: "default", changes: [] }, { name: "Mac", changes: ["hosts"] }, { name: "gpu-box", changes: ["models"] }] };

describe("ProviderSetChoice", () => {
  it("shows no choice while the sets are unknown or the settings define only the default", () => {
    const { container, rerender } = render(<ProviderSetChoice sets={null} selection={NO_SELECTION} onChange={() => {}} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<ProviderSetChoice sets={{ default_set: "default", sets: [SETS.sets[0]] }} selection={NO_SELECTION} onChange={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("lists the default first, then each set, and puts the picked name into the selection", async () => {
    const onChange = vi.fn();
    render(<ProviderSetChoice sets={SETS} selection={NO_SELECTION} onChange={onChange} />);
    const buttons = screen.getAllByRole("radio");
    expect(buttons.map((button) => button.textContent)).toEqual(["default (default)", "Mac", "gpu-box"]);
    expect(buttons[0]).toHaveAttribute("aria-checked", "true");
    await userEvent.click(screen.getByRole("radio", { name: "gpu-box" }));
    expect(onChange).toHaveBeenCalledWith({ ...NO_SELECTION, provider_set: "gpu-box" });
  });

  it("picking the default again sends an empty name, and the chosen set is named in words", async () => {
    const onChange = vi.fn();
    const chosen = { ...NO_SELECTION, provider_set: "Mac" };
    render(<ProviderSetChoice sets={SETS} selection={chosen} onChange={onChange} />);
    expect(screen.getByRole("radio", { name: "Mac" })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(screen.getByRole("radio", { name: "default (default)" }));
    expect(onChange).toHaveBeenCalledWith({ ...chosen, provider_set: "" });
    expect(describeProviderSet(chosen, SETS)).toBe("Mac");
    expect(describeProviderSet(NO_SELECTION, SETS)).toBe("default (default)");
  });
});
