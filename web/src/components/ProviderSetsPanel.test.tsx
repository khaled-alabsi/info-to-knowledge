import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { SettingRow } from "../api";
import { describeHost, describeRunValue, describeValue } from "../settingRunValue";
import ProviderSetsPanel from "./ProviderSetsPanel";

const ROWS: SettingRow[] = [
  { key: "providerSets", path: ["providerSets"], kind: "json", source: "local", comment: "",
    value: { Office: { machine: "the desk", phaseModels: { frames_processing: "frame-gpu" }, framesProcessing: { framesPerCall: 6 } } } },
  { key: "defaultProviderSet", path: ["defaultProviderSet"], value: "default", kind: "text", source: "default", comment: "" },
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
  render(<ProviderSetsPanel rows={ROWS} drafts={{}} onEdit={onEdit} />);
  return onEdit;
}

describe("ProviderSetsPanel", () => {
  it("lists every job with its model from the library and where that model runs, in the default set and in another", () => {
    // The card of a set was a table of overrides to type; each job now shows the library model it calls and where it runs
    renderPanel();
    const fallback = screen.getByRole("group", { name: "provider set default" });
    expect(within(fallback).getByRole("button", { name: /^default: Frames processing/ })).toBeInTheDocument();
    expect(within(fallback).getAllByText("Runs on lmstudio")).toHaveLength(2);
    const office = screen.getByRole("group", { name: "provider set Office" });
    expect(within(office).getByRole("button", { name: /^Office: Frames processing/ })).toBeInTheDocument();
    expect(within(office).getByText("Runs on gpu-box")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\(not set\)|default:\s*$|\(empty\)/);
  });

  it("shows a set only the jobs it changes, and adds one from the Override a job menu defect", async () => {
    // Defect as shown: every set repeated all 11 jobs, so what a set changes was lost among what it left alone. The fix:
    // the default set holds every job, another set only its own, and the others are added from "Override a job"
    const onEdit = renderPanel();
    const office = screen.getByRole("group", { name: "provider set Office" });
    expect(Array.from(office.querySelectorAll(".job-link .job-title")).map((item) => item.textContent)).toEqual(["Frames processing"]);
    expect(within(screen.getByRole("group", { name: "provider set default" })).queryByRole("button", { name: "Override a job" }))
      .not.toBeInTheDocument();
    await userEvent.click(within(office).getByRole("button", { name: "Override a job" }));
    const rows = screen.getAllByRole("menuitem");
    expect(rows.map((item) => item.querySelector(".menu-text > span")?.textContent)).toEqual(["Review"]);
    expect(rows[0].querySelector("svg.icon")).not.toBeNull();
    expect(rows[0].querySelector(".menu-text small")?.textContent).toMatch(/^now the default set's: /);
    await userEvent.click(rows[0]);
    const written = JSON.parse(onEdit.mock.calls[0][1] as string) as { Office: { phaseModels: Record<string, string> } };
    expect(Object.keys(written.Office.phaseModels).sort()).toEqual(["frames_processing", "review"]);
  });

  it("links a job of another set to a model chosen from the library's names, and drops the link on the first choice", async () => {
    // A job is linked to a model by picking its name from a list, never by typing
    const onEdit = renderPanel();
    const office = screen.getByRole("group", { name: "provider set Office" });
    await userEvent.click(within(office).getByRole("button", { name: /^Office: Frames processing/ }));
    // each model once by its own name, a grey line under it, and its server stands once as the heading
    expect(screen.getAllByRole("menuitemradio").map((item) => item.textContent)).toEqual([
      "Same as the default setbig-model on lmstudio", "framebig-model", "documentdoc-model", "frame-gpugpu-model"]);
    expect(Array.from(document.querySelectorAll(".menu-group")).map((item) => item.textContent)).toEqual(["lmstudio", "gpu-box"]);
    expect(document.querySelectorAll(".menu button[role='menuitemradio'] > svg.icon").length).toBeGreaterThan(0);
    await userEvent.click(screen.getByRole("menuitemradio", { name: /^document/ }));
    expect(onEdit).toHaveBeenCalledWith(expect.objectContaining({ key: "providerSets" }),
      expect.stringContaining('"frames_processing": "document"'));
    onEdit.mockClear();
    await userEvent.click(within(office).getByRole("button", { name: /^Office: Frames processing/ }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: /^Same as the default set/ }));
    const written = JSON.parse(onEdit.mock.calls[0][1] as string) as { Office: Record<string, unknown> };
    expect(written.Office.phaseModels).toBeUndefined();
    expect(written.Office.machine).toBe("the desk");
  });

  it("names a model's server once in its menu row, as the group heading defect", async () => {
    // Defect as shown: each row said the server three times ("lmstudio: frame", "lmstudio · big-model", and the heading
    // "lmstudio at http://..."). The fix: the model's name, a grey line, and the server once as the heading
    renderPanel();
    await userEvent.click(within(screen.getByRole("group", { name: "provider set default" }))
      .getByRole("button", { name: /^default: Review/ }));
    // the first option stands under no heading, so it names its model and provider itself
    const [first, ...rows] = screen.getAllByRole("menuitemradio").map((item) => item.textContent ?? "");
    expect(first).toMatch(/^doc-model on lmstudio/);
    for (const row of rows) expect(row).not.toMatch(/lmstudio|gpu-box|http/);
  });

  it("names the default model of a job by its real name and provider, never by its role defect", async () => {
    // Defect as shown: the menu's first option read "The document model", and the owner could not tell which model it is.
    // The fix: it reads the model's real name and the provider it runs on, and a provider job names its built-in tool
    renderPanel();
    await userEvent.click(within(screen.getByRole("group", { name: "provider set default" }))
      .getByRole("button", { name: /^default: Review/ }));
    const names = screen.getAllByRole("menuitemradio").map((item) => item.textContent ?? "");
    expect(names.some((name) => /the document model/i.test(name))).toBe(false);
    expect(names[0]).toContain("doc-model on lmstudio");
  });

  it("changes a job of the default set through the job's own setting", async () => {
    const onEdit = renderPanel();
    const fallback = screen.getByRole("group", { name: "provider set default" });
    await userEvent.click(within(fallback).getByRole("button", { name: /^default: Review/ }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: /^frame-gpu/ }));
    expect(onEdit).toHaveBeenCalledWith(expect.objectContaining({ key: "phaseModels.review" }), "frame-gpu");
  });

  it("copies a set under a new name, from the default set or from another set defect", async () => {
    // A new set started from nothing; every card now has Copy, which asks for a name and keeps the card's links
    const onEdit = renderPanel();
    const office = screen.getByRole("group", { name: "provider set Office" });
    await userEvent.click(within(office).getByRole("button", { name: "copy provider set Office" }));
    const create = within(office).getByRole("button", { name: "Create copy" });
    expect(create).toBeDisabled();
    await userEvent.type(within(office).getByRole("textbox", { name: "name of the copy of Office" }), "Office strict");
    await userEvent.click(create);
    const written = JSON.parse(onEdit.mock.calls[0][1] as string) as Record<string, Record<string, unknown>>;
    expect(written["Office strict"]).toEqual(written.Office);
    expect(written["Office strict"].phaseModels).toEqual({ frames_processing: "frame-gpu" });

    onEdit.mockClear();
    const fallback = screen.getByRole("group", { name: "provider set default" });
    await userEvent.click(within(fallback).getByRole("button", { name: "copy provider set default" }));
    await userEvent.type(within(fallback).getByRole("textbox", { name: "name of the copy of default" }), "Night");
    await userEvent.click(within(fallback).getByRole("button", { name: "Create copy" }));
    const fromDefault = JSON.parse(onEdit.mock.calls[0][1] as string) as Record<string, Record<string, unknown>>;
    expect(fromDefault.Night).toEqual({});
  });

  it("refuses a copy named like a set that exists", async () => {
    renderPanel();
    const office = screen.getByRole("group", { name: "provider set Office" });
    await userEvent.click(within(office).getByRole("button", { name: "copy provider set Office" }));
    await userEvent.type(within(office).getByRole("textbox", { name: "name of the copy of Office" }), "default");
    expect(within(office).getByRole("button", { name: "Create copy" })).toBeDisabled();
    expect(within(office).getByText("a set has this name")).toBeInTheDocument();
  });

  it("shows a value an older set changes beside the default's, with Use default", async () => {
    const onEdit = renderPanel();
    const office = screen.getByRole("group", { name: "provider set Office" });
    expect(within(office).getByText("default: 4")).toBeInTheDocument();
    await userEvent.click(within(office).getByRole("button", { name: "stop changing Frames per call in Office" }));
    const written = JSON.parse(onEdit.mock.calls[0][1] as string) as { Office: Record<string, unknown> };
    expect(written.Office.framesProcessing).toBeUndefined();
  });

  it("puts the choice of the set runs use on each set's own card, with no bar above the cards defect", async () => {
    // Defect as shown: a separate bar above the cards held "Used when a job names no set". The fix: each card has a button
    // "Use for runs that pick no set", and the card in use says so
    const onEdit = renderPanel();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Used when a job names no set/ })).not.toBeInTheDocument();
    const fallback = screen.getByRole("group", { name: "provider set default" });
    expect(within(fallback).getByText("Used by runs that pick no set")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "use provider set Office for runs that pick no set" }));
    expect(onEdit).toHaveBeenCalledWith(expect.objectContaining({ key: "defaultProviderSet" }), "Office");
  });

  it("has no Machine box on a set, and removes a set with a real button defect", () => {
    // The owner: "not a machine, its set name". The machine a set runs on is read from the providers its jobs use
    renderPanel();
    const office = screen.getByRole("group", { name: "provider set Office" });
    expect(screen.queryByRole("button", { name: /^Machine of/ })).not.toBeInTheDocument();
    expect(within(office).queryByText(/^Machine$/)).not.toBeInTheDocument();
    expect(within(office).getByRole("button", { name: "remove provider set Office" })).toHaveClass("btn", "danger");
  });

  it("shows each job as one card holding its title, its model menu and where it runs, with no fast workflow models", () => {
    renderPanel();
    const fallback = screen.getByRole("group", { name: "provider set default" });
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

  it("says a server by its name exactly as written, never its address and never respelled defect", () => {
    // Defect as shown: a server named "gpu" showed as "GPU" on the card but "gpu" in the settings, so one server had two
    // names. The fix: the name as written, in every menu and under every job
    expect(describeHost("lmstudio")).toBe("lmstudio");
    expect(describeHost("gpu")).toBe("gpu");
    expect(describeHost("gpu-box")).toBe("gpu-box");
  });
});
