import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import ManagerPanel from "./ManagerPanel";

describe("ManagerPanel", () => {
  it("shows only its menu, and a tool chosen from it opens elsewhere and is marked", async () => {
    const onOpenTool = vi.fn();
    const { rerender } = render(<ManagerPanel openTool={null} onOpenTool={onOpenTool} />);
    expect(screen.getAllByRole("button").map((button) => button.textContent?.trim())).toEqual(["Download", "Rename", "Compress videos"]);
    await userEvent.click(screen.getByRole("button", { name: /Download/ }));
    expect(onOpenTool).toHaveBeenCalledWith("download");
    rerender(<ManagerPanel openTool="download" onOpenTool={onOpenTool} />);
    expect(screen.getByRole("button", { name: /Download/ })).toHaveAttribute("aria-current", "true");
  });
});
