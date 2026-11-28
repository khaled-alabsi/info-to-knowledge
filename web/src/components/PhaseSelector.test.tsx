import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { NO_SELECTION } from "../api";
import type { PhaseSelection } from "../api";
import { PHASES, installServer } from "../test/fakeServer";
import PhaseSelector, { describeSelection } from "./PhaseSelector";
import { usePhaseModels } from "./RunChoices";

const NO_MODELS = { models: [], phases: [] };

// The panel as a page holds it: the lists read once by the page, the choice kept by it and named in its summary
function ChoosePhases({ phases, onChosen }: { phases: typeof PHASES; onChosen: (selection: PhaseSelection) => void }) {
  const [selection, setSelection] = useState(NO_SELECTION);
  const { phaseModels } = usePhaseModels();
  onChosen(selection);
  return <><PhaseSelector phases={phases} phaseModels={phaseModels} selection={selection}
    onChange={setSelection} /><output>{describeSelection(selection, phaseModels)}</output></>;
}

// Renders the panel; the returned object always holds the latest choice
function renderChoosePhases(phases = PHASES): { selection: PhaseSelection } {
  const chosen = { selection: NO_SELECTION };
  render(<ChoosePhases phases={phases} onChosen={(selection) => { chosen.selection = selection; }} />);
  return chosen;
}

describe("describeSelection", () => {
  it("names the phases a run covers", () => {
    expect(describeSelection(NO_SELECTION, NO_MODELS)).toBe("every phase");
    expect(describeSelection({ ...NO_SELECTION, only_phase: 3, to_phase: 4, is_dry_run: true }, NO_MODELS)).toBe("phase 3 · dry run");
    expect(describeSelection({ ...NO_SELECTION, from_phase: 2, to_phase: 4 }, NO_MODELS)).toBe("from 2 · to 4");
    expect(describeSelection({ ...NO_SELECTION, profile: "strict" }, NO_MODELS)).toBe("every phase · profile: strict");
  });

  it("numbers a phase given another model as the server numbers it", () => {
    const phaseModels = { models: [], phases: [{ phase: "frames_xyz", number: 7, title: "frames xyz", default: "frame" }] };
    expect(describeSelection({ ...NO_SELECTION, phase_models: { frames_xyz: "acme-small" } }, phaseModels))
      .toBe("every phase · phase 7: acme-small");
  });
});

describe("PhaseSelector", () => {
  it("takes tags for the run, suggested from the tags in use, and names them in the summary", async () => {
    installServer({ "GET /api/tags": { tags: ["AcmeSync"], profiles: [{ name: "conversation", aliases: ["chat"] }] } });
    const chosen = renderChoosePhases();
    const box = screen.getByRole("combobox", { name: "add a tag" });
    await userEvent.type(box, "ch");
    await userEvent.click(await screen.findByRole("option", { name: "chat" }));
    await userEvent.type(box, "week 42{Enter}");
    expect(chosen.selection.tags).toEqual(["chat", "week 42"]);
    expect(screen.getByText("every phase · tags: chat, week 42")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "remove tag chat" }));
    expect(chosen.selection.tags).toEqual(["week 42"]);
  });

  it("folds the phases and the run settings so that opening one closes the other defect", async () => {
    // Defect: the phases and "Run with" were open together, and the panel ran past the window's height.
    // Fix: an accordion: the phases open first, and opening "Run with" folds them, and the other way round.
    renderChoosePhases();
    const [phasesPart, more] = [...document.querySelectorAll<HTMLDetailsElement>(".phase-more")];
    expect([phasesPart.open, more.open]).toEqual([true, false]);
    expect(more.querySelector("summary")).toHaveTextContent("Run with default settings, default models");
    await userEvent.click(more.querySelector("summary")!);
    expect([phasesPart.open, more.open]).toEqual([false, true]);
    await userEvent.click(screen.getByRole("checkbox", { name: /Dry run/ }));
    expect(more.querySelector("summary")).toHaveTextContent("Run with dry run, default settings, default models");
    await userEvent.click(phasesPart.querySelector("summary")!);
    expect([phasesPart.open, more.open]).toEqual([true, false]);
  });

  it("chooses every phase, from a phase, or one phase alone, with a last phase and a dry run", async () => {
    const chosen = renderChoosePhases();
    expect(screen.getByRole("radio", { name: "Every phase" })).toHaveAttribute("aria-checked", "true");
    expect(screen.queryByRole("radiogroup", { name: "phase" })).not.toBeInTheDocument();
    await pick("stop after", /^4 ·/);
    expect(screen.getByRole("status")).toHaveTextContent("phases 1 to 4");

    await userEvent.click(screen.getByRole("radio", { name: "From" }));
    expect(screen.getByRole("status")).toHaveTextContent("from 1 · to 4");
    await pick("phase", /^3 ·/);
    expect(screen.getByRole("status")).toHaveTextContent("from 3 · to 4");

    await userEvent.click(screen.getByRole("radio", { name: "Only" }));
    expect(chosen.selection).toMatchObject({ only_phase: 3, from_phase: null, to_phase: null });
    expect(screen.queryByRole("radiogroup", { name: "stop after" })).not.toBeInTheDocument();
    await pick("phase", /^5 ·/);
    expect(screen.getByRole("status")).toHaveTextContent("phase 5");

    await userEvent.click(screen.getByRole("radio", { name: "Every phase" }));
    await userEvent.click(screen.getByRole("checkbox", { name: /Dry run/ }));
    await pick("stop after", "the last phase");
    expect(chosen.selection).toEqual({ ...NO_SELECTION, is_dry_run: true });
    expect(screen.getByRole("status")).toHaveTextContent("every phase · dry run");
  });

  it("offers no phase the flow never runs as the place to stop defect", () => {
    // Defect: a PDF read as text only, which has no review and no critique collection, offered both under Stop after
    // Fix: a phase that is off is left out of Stop after
    renderChoosePhases(PHASES.map((phase) => (phase.number >= 5 ? { ...phase, is_on: false } : phase)));
    const stops = within(screen.getByRole("radiogroup", { name: "stop after" })).getAllByRole("radio").map((radio) => radio.textContent);
    expect(stops).toHaveLength(5);
    expect(stops.some((text) => /^5 ·|^6 ·/.test(text ?? ""))).toBe(false);
  });

  it("starts from phase 1 when the phase list has not loaded", async () => {
    const chosen = renderChoosePhases([]);
    await userEvent.click(screen.getByRole("radio", { name: "From" }));
    expect(chosen.selection.from_phase).toBe(1);
  });
});

// Chooses a phase in one of the panel's lists of phase buttons
async function pick(list: string, name: RegExp | string) {
  await userEvent.click(within(screen.getByRole("radiogroup", { name: list })).getByRole("radio", { name }));
}

describe("PhaseSelector settings", () => {
  it("has no settings menu: the settings files, strict among them, are chosen on the Settings page", async () => {
    // The run form never lists a settings file; strict is the file `playbook.strict.json` and shows where profiles are offered
    installServer({ "GET /api/profiles": { profiles: ["strict"], server_profile: "" } });
    renderChoosePhases();
    await screen.findByRole("radio", { name: "Every phase" });
    expect(screen.queryByRole("radiogroup", { name: "Settings" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: "profile: strict" })).not.toBeInTheDocument();
  });

  it("offers each phase the configured models, puts the choice on the run and names it in the summary", async () => {
    installServer({ "GET /api/phase-models": {
      models: [{ name: "frame", spec: "lmstudio:acme-big" }, { name: "document", spec: "lmstudio:acme-big" },
        { name: "acme-small", spec: "lmstudio:acme-small-vl" }],
      phases: [{ phase: "frames_processing", number: 3, title: "frames processing", default: "frame" },
        { phase: "finalize", number: 4, title: "document writing", default: "frame" }] } });
    const chosen = renderChoosePhases();
    const frames = await screen.findByRole("radiogroup", { name: "model of frames processing" });
    expect(within(frames).getAllByRole("radio").map((option) => option.textContent))
      .toEqual(["acme-big (default)", "acme-small-vl"]);
    await userEvent.click(within(frames).getByRole("radio", { name: "acme-small-vl" }));
    expect(chosen.selection.phase_models).toEqual({ frames_processing: "acme-small" });
    expect(screen.getByText("every phase · phase 3: acme-small")).toBeInTheDocument();
    await userEvent.click(within(frames).getByRole("radio", { name: "acme-big (default)" }));
    expect(chosen.selection.phase_models).toEqual({});
    expect(screen.getByRole("radiogroup", { name: "model of document writing" })).toBeInTheDocument();
  });

  it("shows no model choice when one model is configured", async () => {
    installServer({ "GET /api/phase-models": { models: [{ name: "frame", spec: "lmstudio:acme-big" }],
      phases: [{ phase: "frames_processing", number: 3, title: "frames processing", default: "frame" }] } });
    renderChoosePhases();
    await screen.findByRole("radio", { name: "Every phase" });
    expect(screen.queryByRole("radiogroup", { name: "model of frames processing" })).not.toBeInTheDocument();
  });
});
