import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { installServer, makeRun } from "../test/fakeServer";
import RenameView from "./RenameView";

const RUNS = [
  makeRun({ name: "Recording-20260101", topic: "Gateway refund rules" }),
  makeRun({ name: "Recording-20260102", topic: "Jonas onboarding", display_name: "Jonas onboarding" }),
  makeRun({ name: "Recording-20260103", topic: "" }),
];

describe("RenameView", () => {
  it("lists the runs with an AI name and shows the ticked ones under it, or under their file name again", async () => {
    let records = [{ name: "gateway-refund-rules", display_name: "Gateway refund rules", outcome: "renamed" }];
    const calls = installServer({ "POST /api/runs/display-names": () => ({ exit_code: 0, output: "", records }) });
    const onChanged = vi.fn();
    const onMessage = vi.fn();
    render(<RenameView runs={RUNS} onBack={() => {}} onChanged={onChanged} onMessage={onMessage} />);
    expect(screen.getByRole("tab", { name: "File name (1)" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByText("Recording-20260103")).not.toBeInTheDocument();
    expect(screen.getByText("AI name: Gateway refund rules")).toBeInTheDocument();
    expect(screen.queryByText("shown under its AI name")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use AI name" })).toBeDisabled();

    await userEvent.click(screen.getByText("Recording-20260101"));
    await userEvent.click(screen.getByRole("button", { name: /Use AI name for 1/ }));
    expect(calls.at(-1)?.body).toEqual({ runs: [{ root: "knowledge", name: "Recording-20260101" }], is_by_topic: true });
    expect(onMessage).toHaveBeenCalledWith("1 of 1 run renamed to their AI name", false);
    expect(onChanged).toHaveBeenCalled();

    // a run kept under its name is named with why, and the message stays until it is closed
    records = [{ name: "Recording-20260102", display_name: "", outcome: "kept: a run writes into it" }];

    await userEvent.click(screen.getByRole("tab", { name: "AI name (1)" }));
    await userEvent.click(screen.getByText("Jonas onboarding"));
    await userEvent.click(screen.getByRole("button", { name: /Use file name/ }));
    expect(calls.at(-1)?.body).toEqual({ runs: [{ root: "knowledge", name: "Recording-20260102" }], is_by_topic: false });
    expect(onMessage).toHaveBeenLastCalledWith("0 of 1 run renamed to their file name · Recording-20260102: kept: a run writes into it", true);
  });

  it("names a run still writing as renamed when its run ends, never as an error defect", async () => {
    // Renaming a run that was writing showed a red "kept: a run is writing into it", and the run never got its name.
    // Now the server renames it when the run ends, and the page says so in a message that is no error.
    const records = [{ name: "Recording-20260101", display_name: "Gateway refund rules", outcome: "renamed when its run ends" }];
    installServer({ "POST /api/runs/display-names": () => ({ exit_code: 0, output: "", records }) });
    const onMessage = vi.fn();
    render(<RenameView runs={RUNS} onBack={() => {}} onChanged={() => {}} onMessage={onMessage} />);

    await userEvent.click(screen.getByText("Recording-20260101"));
    await userEvent.click(screen.getByRole("button", { name: /Use AI name for 1/ }));
    expect(onMessage).toHaveBeenLastCalledWith(
      "0 of 1 run renamed to their AI name · Recording-20260101: renamed when its run ends", false);
  });

  it("offers a run already under its AI name only the way back, never its AI name again defect", async () => {
    // Every run was offered "Use AI name", 55 of 57 already shown under it, so the list read as still to rename.
    // Now the runs are two tabs by the name they are under, each with only its own button.
    installServer({});
    render(<RenameView runs={RUNS} onBack={() => {}} onChanged={() => {}} onMessage={() => {}} />);
    await userEvent.click(screen.getByRole("tab", { name: "AI name (1)" }));
    expect(screen.queryByText("Recording-20260101")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "tick every run shown" }));
    expect(screen.queryByRole("button", { name: /Use AI name/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use file name for 1" })).toBeEnabled();
  });
});
