import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { SettingRow } from "../api";
import { describeHost, describeRunValue, describeValue } from "../settingRunValue";
import WorkerSetsPanel from "./WorkerSetsPanel";

const ROWS: SettingRow[] = [
  { key: "workerSets", path: ["workerSets"], kind: "json", source: "local", comment: "",
    value: { Office: { machine: "the desk", phaseModels: { frames_processing: "frame-gpu" }, framesProcessing: { framesPerCall: 6 } } } },
  { key: "defaultWorkerSet", path: ["defaultWorkerSet"], value: "default", kind: "text", source: "default", comment: "" },
  { key: "machine", path: ["machine"], value: "the laptop", kind: "text", source: "local", comment: "" },
  { key: "hosts", path: ["hosts"], kind: "json", source: "local", comment: "",
    value: { lmstudio: { url: "http://localhost:1234/v1" }, "gpu-box": { url: "http://gpu-box.local:8090/v1" } } },
  { key: "models.frame.spec", path: ["models", "frame", "spec"], value: "lmstudio:big-model", kind: "text", source: "code", comment: "" },
  { key: "models.frame-gpu.spec", path: ["models", "frame-gpu", "spec"], value: "lmstudio:gpu-model", kind: "text", source: "local", comment: "" },
  { key: "models.frame-gpu.host", path: ["models", "frame-gpu", "host"], value: "gpu-box", kind: "text", source: "local", comment: "" },
  { key: "models.document.spec", path: ["models", "document", "spec"], value: "lmstudio:doc-model", kind: "text", source: "code", comment: "" },
  { key: "phaseModels.frames_processing", path: ["phaseModels", "frames_processing"], value: "", kind: "text", source: "code",
    comment: "", resolved: "frame", resolved_note: "the phase's own model" },
  { key: "phaseModels.review", path: ["phaseModels", "review"], value: "", kind: "text", source: "code",
    comment: "", resolved: "document", resolved_note: "the phase's own model" },
  { key: "framesProcessing.framesPerCall", path: ["framesProcessing", "framesPerCall"], value: 4, kind: "number", source: "default", comment: "" },
];

function renderPanel(onEdit = vi.fn()) {
  render(<WorkerSetsPanel rows={ROWS} drafts={{}} onEdit={onEdit} />);
  return onEdit;
}

describe("WorkerSetsPanel", () => {
  it("lists every job with its model from the library and where that model runs, in the default set and in another", () => {
    // The card of a set was a table of overrides to type; each job now shows the library model it calls and where it runs
    renderPanel();
    const fallback = screen.getByRole("group", { name: "worker set default" });
    expect(within(fallback).getByRole("button", { name: "default: Frames processing: the frame model" })).toBeInTheDocument();
    expect(within(fallback).getAllByText(/^Runs on lmstudio · big-model$/)).toHaveLength(1);
    expect(within(fallback).getAllByText(/^Runs on lmstudio · doc-model$/)).toHaveLength(1);
    const office = screen.getByRole("group", { name: "worker set Office" });
    expect(within(office).getByRole("button", { name: "Office: Frames processing: gpu-box: frame-gpu" })).toBeInTheDocument();
    expect(within(office).getByText("Runs on gpu-box · gpu-model")).toBeInTheDocument();
    expect(within(office).getByRole("button", { name: "Office: Review: same as default set: document" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\(not set\)|default:\s*$|\(empty\)/);
  });

  it("links a job of another set to a model chosen from the library's names, and drops the link on the first choice", async () => {
    // A job is linked to a model by picking its name from a list, never by typing
    const onEdit = renderPanel();
    const office = screen.getByRole("group", { name: "worker set Office" });
    await userEvent.click(within(office).getByRole("button", { name: "Office: Review: same as default set: document" }));
    expect(screen.getAllByRole("menuitemradio").map((item) => item.textContent)).toEqual([
      "same as default set: document", "lmstudio: framelmstudio · big-model", "lmstudio: documentlmstudio · doc-model",
      "gpu-box: frame-gpugpu-box · gpu-model"]);
    // the options stand under a heading a server, each named "server: model"
    expect(Array.from(document.querySelectorAll(".menu-group")).map((item) => item.textContent)).toEqual(["lmstudio", "gpu-box"]);
    await userEvent.click(screen.getByRole("menuitemradio", { name: /^gpu-box: frame-gpu/ }));
    expect(onEdit).toHaveBeenCalledWith(expect.objectContaining({ key: "workerSets" }), expect.stringContaining('"review": "frame-gpu"'));
    onEdit.mockClear();
    await userEvent.click(within(office).getByRole("button", { name: "Office: Frames processing: gpu-box: frame-gpu" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: /^same as default set/ }));
    const written = JSON.parse(onEdit.mock.calls[0][1] as string) as { Office: Record<string, unknown> };
    expect(written.Office.phaseModels).toBeUndefined();
    expect(written.Office.machine).toBe("the desk");
  });

  it("changes a job of the default set through the job's own setting", async () => {
    const onEdit = renderPanel();
    const fallback = screen.getByRole("group", { name: "worker set default" });
    await userEvent.click(within(fallback).getByRole("button", { name: "default: Review: the document model" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: /^gpu-box: frame-gpu/ }));
    expect(onEdit).toHaveBeenCalledWith(expect.objectContaining({ key: "phaseModels.review" }), "frame-gpu");
  });

  it("copies a set under a new name, from the default set or from another set defect", async () => {
    // A new set started from nothing; every card now has Copy, which asks for a name and keeps the card's links
    const onEdit = renderPanel();
    const office = screen.getByRole("group", { name: "worker set Office" });
    await userEvent.click(within(office).getByRole("button", { name: "copy worker set Office" }));
    const create = within(office).getByRole("button", { name: "Create copy" });
    expect(create).toBeDisabled();
    await userEvent.type(within(office).getByRole("textbox", { name: "name of the copy of Office" }), "Office strict");
    await userEvent.click(create);
    const written = JSON.parse(onEdit.mock.calls[0][1] as string) as Record<string, Record<string, unknown>>;
    expect(written["Office strict"]).toEqual(written.Office);
    expect(written["Office strict"].phaseModels).toEqual({ frames_processing: "frame-gpu" });

    onEdit.mockClear();
    const fallback = screen.getByRole("group", { name: "worker set default" });
    await userEvent.click(within(fallback).getByRole("button", { name: "copy worker set default" }));
    await userEvent.type(within(fallback).getByRole("textbox", { name: "name of the copy of default" }), "Night");
    await userEvent.click(within(fallback).getByRole("button", { name: "Create copy" }));
    const fromDefault = JSON.parse(onEdit.mock.calls[0][1] as string) as Record<string, Record<string, unknown>>;
    expect(fromDefault.Night).toEqual({});
  });

  it("refuses a copy named like a set that exists", async () => {
    renderPanel();
    const office = screen.getByRole("group", { name: "worker set Office" });
    await userEvent.click(within(office).getByRole("button", { name: "copy worker set Office" }));
    await userEvent.type(within(office).getByRole("textbox", { name: "name of the copy of Office" }), "default");
    expect(within(office).getByRole("button", { name: "Create copy" })).toBeDisabled();
    expect(within(office).getByText("a set has this name")).toBeInTheDocument();
  });

  it("shows a value an older set changes beside the default's, with Use default", async () => {
    const onEdit = renderPanel();
    const office = screen.getByRole("group", { name: "worker set Office" });
    expect(within(office).getByText("default: 4")).toBeInTheDocument();
    await userEvent.click(within(office).getByRole("button", { name: "stop changing framesProcessing.framesPerCall in Office" }));
    const written = JSON.parse(onEdit.mock.calls[0][1] as string) as { Office: Record<string, unknown> };
    expect(written.Office.framesProcessing).toBeUndefined();
  });

  it("picks the set a job takes from one menu above the cards, replacing a radio on each card defect", async () => {
    // Each card had a radio "used when a job names no set". One menu above the cards now holds the choice
    const onEdit = renderPanel();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Used when a job names no set: default" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: "Office" }));
    expect(onEdit).toHaveBeenCalledWith(expect.objectContaining({ key: "defaultWorkerSet" }), "Office");
  });

  it("has no Machine box on a set, and removes a set with a real button defect", () => {
    // The owner: "not a machine, its set name". The machine a set runs on is read from the providers its jobs use
    renderPanel();
    const office = screen.getByRole("group", { name: "worker set Office" });
    expect(screen.queryByRole("button", { name: /^Machine of/ })).not.toBeInTheDocument();
    expect(within(office).queryByText(/^Machine$/)).not.toBeInTheDocument();
    expect(within(office).getByRole("button", { name: "remove worker set Office" })).toHaveClass("btn", "danger");
  });

  it("shows each job as one card holding its title, its model menu and where it runs, with no fast workflow models", () => {
    renderPanel();
    const fallback = screen.getByRole("group", { name: "worker set default" });
    const cards = fallback.querySelectorAll(".job-link");
    expect(cards).toHaveLength(2);
    for (const card of Array.from(cards)) {
      expect(within(card as HTMLElement).getAllByRole("button")).toHaveLength(1);
      expect(card.textContent).toMatch(/Runs on/);
    }
    expect(document.body.textContent).not.toMatch(/Fast workflow/);
  });
});

describe("what the page says for an empty value", () => {
  it("names what a run uses, with where it comes from, and never prints nothing", () => {
    expect(describeRunValue({ kind: "text", resolved: "lmstudio", resolved_note: "from the model's provider" }))
      .toBe("lmstudio (from the model's provider)");
    expect(describeRunValue({ kind: "text", resolved: null, resolved_note: "found from the recording" })).toBe("found from the recording");
    expect(describeRunValue({ kind: "text" })).toBe("automatic");
    expect(describeRunValue({ kind: "long-text" })).toBe("not used");
    expect(describeValue(null, "")).toBe("automatic");
    expect(describeValue(null, 4)).toBe("4");
  });

  it("says a provider by its plain name, never its address, a short name in capitals", () => {
    expect(describeHost("lmstudio")).toBe("lmstudio");
    expect(describeHost("gpu")).toBe("GPU");
    expect(describeHost("gpu-box")).toBe("gpu-box");
  });
});
