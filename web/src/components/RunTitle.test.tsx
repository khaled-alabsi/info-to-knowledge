import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import RunTitle from "./RunTitle";

describe("RunTitle", () => {
  it("saves the name typed after the pencil, leaves it on Escape, and saves the folder's name as empty", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RunTitle shownName="Recording-20260104" folderName="Recording-20260104" onSave={onSave} />);
    await userEvent.click(screen.getByRole("button", { name: "rename" }));
    await userEvent.clear(screen.getByLabelText("name shown"));
    await userEvent.type(screen.getByLabelText("name shown"), "Gateway launch plan{Enter}");
    expect(onSave).toHaveBeenLastCalledWith("Gateway launch plan");

    await userEvent.click(screen.getByRole("button", { name: "rename" }));
    await userEvent.type(screen.getByLabelText("name shown"), " more{Escape}");
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading")).toHaveTextContent("Recording-20260104");
  });

  it("shows a named run under its name, and its folder's name typed back is saved as no name", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(<RunTitle shownName="Gateway launch plan" folderName="Recording-20260104" onSave={onSave} />);
    expect(screen.getByRole("heading")).toHaveAttribute("title", "Gateway launch plan\nRecording-20260104");
    await userEvent.click(screen.getByRole("button", { name: "rename" }));
    await userEvent.clear(screen.getByLabelText("name shown"));
    await userEvent.type(screen.getByLabelText("name shown"), "Recording-20260104{Enter}");
    expect(onSave).toHaveBeenLastCalledWith("");
  });
});
