import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { InstructionCatalogue, InstructionFile, InstructionVersion } from "../instructionsApi";
import { installServer } from "../test/fakeServer";
import InstructionsView from "./InstructionsView";

// Invented texts: a critique instruction holding a shared guideline and the profile anchor, and the team's terms
const CRITIQUE = "instructions/critique.md";
const TERMS = "instructions/_shared/known-terms.md";
const TAKEAWAY = "instructions/takeaway.md";
const CRITIQUE_TEXT = "# Your job\n\nName what went wrong in the Gateway notes.\n\n{{SHARED:known-terms}}\n\n{{INJECT:base}}\n";
const ADDED_LINE = "Name the AcmeSync retry count as written.";

const CATALOGUE: InstructionCatalogue = {
  workflows: [
    { key: "video", title: "Video", variants: [{ key: "quality", title: "quality" }, { key: "fast-processing", title: "fast processing" }],
      phases: [{ number: 3, title: "frames processing", steps: [
        { key: "critique", title: "Critique", who: "Phase 3 of a video, after each batch.", variants: ["quality"], files: [CRITIQUE, TERMS] },
        { key: "fast_frame_note", title: "Frame description", who: "The fast workflow.", variants: ["fast-processing"],
          files: ["instructions/fast_frame_note.md"] },
      ] }] },
    { key: "audio", title: "Audio", variants: [{ key: "takeaway", title: "takeaway" }],
      phases: [{ number: 4, title: "document writing", steps: [
        { key: "takeaway", title: "Takeaway", who: "One call.", variants: ["takeaway"], files: [TAKEAWAY] }] }] },
  ],
  files: [
    { id: CRITIQUE, title: "Critique", kind: "instruction", used_by: [{ workflow: "video", step: "critique" }], is_edited: false, is_default_changed: false },
    { id: TERMS, title: "Shared guideline: known terms", kind: "shared", used_by: [{ workflow: "video", step: "critique" }], is_edited: true, is_default_changed: false },
    { id: "instructions/fast_frame_note.md", title: "Frame description", kind: "instruction", used_by: [], is_edited: false, is_default_changed: false },
    { id: TAKEAWAY, title: "Takeaway", kind: "instruction", used_by: [{ workflow: "audio", step: "takeaway" }], is_edited: false, is_default_changed: false },
  ],
};

function buildFile(id: string, text: string, overrides: Partial<InstructionFile> = {}): InstructionFile {
  const entry = CATALOGUE.files.find((file) => file.id === id)!;
  return { ...entry, default: text, text, default_at_edit: "", versions: 0, ...overrides };
}

const VERSIONS: InstructionVersion[] = [
  { at: "20261106T190700.000000", kind: "edit", account: "jonas", note: "retry counts", size_bytes: 120 }];

function serve(routes: Record<string, unknown> = {}) {
  const query = (id: string) => `id=${encodeURIComponent(id)}`;
  return installServer({
    "GET /api/instructions": CATALOGUE,
    [`GET /api/instructions/file?${query(CRITIQUE)}`]: buildFile(CRITIQUE, CRITIQUE_TEXT),
    [`GET /api/instructions/file?${query(TERMS)}`]: buildFile(TERMS, "- **BA**: business analyst\n"),
    [`GET /api/instructions/versions?${query(CRITIQUE)}`]: [],
    [`GET /api/instructions/versions?${query(TERMS)}`]: VERSIONS,
    [`GET /api/instructions/version?${query(TERMS)}&at=${VERSIONS[0].at}`]: {
      ...VERSIONS[0], text: "- **BA**: business analyst\n- **PO**: product owner\n", default_text: "- **BA**: business analyst\n" },
    "PUT /api/instructions/file": () => ({ is_saved: true, problems: [], at: "20261106T191000.000000", is_changed: true }),
    "POST /api/instructions/reset": { at: "20261106T191100.000000", is_changed: true },
    "POST /api/instructions/preview": (body: Record<string, unknown>) => ({ text: `PROMPT ${String(body.text || "in force")}`, error: "" }),
    ...routes,
  } as Parameters<typeof installServer>[0]);
}

async function openCritique() {
  const onMessage = vi.fn();
  render(<InstructionsView onMessage={onMessage} />);
  await userEvent.click(await screen.findByText("Critique", { selector: "summary span" }));
  // jsdom opens no <details> on a click on its summary, so the step's files are looked for as a closed list's
  await userEvent.click(screen.getByRole("button", { name: "Critique (instruction), default", hidden: true }));
  await screen.findByRole("heading", { name: "Critique", level: 3 });
  return onMessage;
}

describe("Instructions", () => {
  it("shows a workflow's phases and steps, and a file rendered with its markers drawn as chips", async () => {
    serve();
    await openCritique();
    expect(screen.getByText("Phase 3 · frames processing")).toBeInTheDocument();
    expect(screen.getByText("Phase 3 of a video, after each batch.")).toBeInTheDocument();
    expect(screen.getByText("Name what went wrong in the Gateway notes.")).toBeInTheDocument();
    expect(screen.getByText("the rules of the recording's profile or medium go here")).toBeInTheDocument();
    // the shared guideline opens from its chip
    await userEvent.click(screen.getByRole("button", { name: "Shared guideline: known terms ↗" }));
    expect(await screen.findByRole("heading", { name: "Shared guideline: known terms", level: 3 })).toBeInTheDocument();
  });

  it("lists another workflow's steps, and hides a step a chosen profile does not send", async () => {
    serve();
    render(<InstructionsView onMessage={vi.fn()} />);
    expect(await screen.findByText("Frame description", { selector: "summary span" })).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox"), "quality");
    expect(screen.queryByText("Frame description", { selector: "summary span" })).not.toBeInTheDocument();
    const search = screen.getByRole("searchbox", { name: "find a step or a file" });
    await userEvent.type(search, "nothing like this");
    expect(screen.queryByText("Critique", { selector: "summary span" })).not.toBeInTheDocument();
    await userEvent.clear(search);
    await userEvent.click(screen.getByRole("tab", { name: "Audio" }));
    expect(await screen.findByText("Phase 4 · document writing")).toBeInTheDocument();
  });

  it("saves an edit with its note, and shows a refusal's reasons with the draft kept", async () => {
    let answer: unknown = { is_saved: false, problems: ["instructions/critique.md: keep {{INJECT:base}}, which the program fills"] };
    const calls = serve({ "PUT /api/instructions/file": () => answer });
    const onMessage = await openCritique();
    await userEvent.click(screen.getByRole("tab", { name: "Edit" }));
    const source = screen.getByRole("textbox", { name: "the text of Critique" });
    await userEvent.type(source, ADDED_LINE);
    expect(screen.getAllByText("not saved").length).toBeGreaterThan(0);
    await userEvent.type(screen.getByRole("textbox", { name: "why you changed it" }), "retry counts");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/keep \{\{INJECT:base\}\}/)).toBeInTheDocument();
    expect((source as HTMLTextAreaElement).value).toContain(ADDED_LINE);
    answer = { is_saved: true, problems: [], at: "20261106T191000.000000", is_changed: true };
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onMessage).toHaveBeenCalledWith("Critique saved. The next call of a running run reads it."));
    const put = calls.filter((call) => call.method === "PUT").at(-1)!;
    expect(put.body).toEqual({ id: CRITIQUE, text: CRITIQUE_TEXT + ADDED_LINE, note: "retry counts", kind: "edit" });
  });

  it("puts a shared guideline's text in place of its include, for this file alone", async () => {
    serve();
    await openCritique();
    await userEvent.click(screen.getByRole("tab", { name: "Edit" }));
    await userEvent.click(screen.getByRole("button", { name: "Put the text of known terms here" }));
    const source = screen.getByRole("textbox", { name: "the text of Critique" }) as HTMLTextAreaElement;
    await waitFor(() => expect(source.value).toContain("- **BA**: business analyst"));
    expect(source.value).not.toContain("{{SHARED:known-terms}}");
    expect(screen.queryByRole("button", { name: "Put the text of known terms here" })).not.toBeInTheDocument();
  });

  it("compares the default with a draft, the added line marked", async () => {
    serve();
    await openCritique();
    await userEvent.click(screen.getByRole("tab", { name: "Edit" }));
    await userEvent.type(screen.getByRole("textbox", { name: "the text of Critique" }), ADDED_LINE);
    await userEvent.click(screen.getByRole("tab", { name: "Compare" }));
    const [, right] = screen.getAllByRole("combobox").slice(-2);
    await userEvent.selectOptions(right, "draft");
    const added = await screen.findByText(ADDED_LINE, { exact: false, selector: "pre.added, pre.added *" });
    expect(added).toBeInTheDocument();
    expect(screen.getByText("One place differs.", { exact: false })).toBeInTheDocument();
    await userEvent.selectOptions(right, "default");
    expect(screen.getByText("The two texts are the same.")).toBeInTheDocument();
  });

  it("restores an earlier version from the history as a restore, and resets an edited file", async () => {
    const calls = serve();
    render(<InstructionsView onMessage={vi.fn()} />);
    await userEvent.click(await screen.findByText("Critique", { selector: "summary span" }));
    await userEvent.click(screen.getByRole("button", { name: "Shared guideline: known terms (shared guideline), edited", hidden: true }));
    await userEvent.click(await screen.findByRole("tab", { name: "History (1)" }));
    const row = screen.getByText("retry counts").closest("li") as HTMLElement;
    expect(within(row).getByText("by jonas")).toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: /Restore/ }));
    expect((await screen.findByRole("textbox", { name: /the text of/ }) as HTMLTextAreaElement).value).toContain("product owner");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((call) => call.method === "PUT")).toBe(true));
    expect(calls.find((call) => call.method === "PUT")?.body).toMatchObject({ kind: "restore", id: TERMS });
    await userEvent.click(screen.getByRole("button", { name: /Reset to default/ }));
    await waitFor(() => expect(calls.some((call) => call.method === "POST" && call.url === "/api/instructions/reset")).toBe(true));
  });

  it("shows the step's full prompt, built with the draft when there is one", async () => {
    const calls = serve();
    await openCritique();
    await userEvent.click(screen.getByRole("tab", { name: "Full prompt" }));
    expect(await screen.findByText("PROMPT in force")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Edit" }));
    await userEvent.type(screen.getByRole("textbox", { name: "the text of Critique" }), ADDED_LINE);
    await userEvent.click(screen.getByRole("tab", { name: "Full prompt" }));
    await waitFor(() => expect(calls.filter((call) => call.url === "/api/instructions/preview").at(-1)?.body)
      .toEqual({ step: "critique", workflow: "video", id: CRITIQUE, text: CRITIQUE_TEXT + ADDED_LINE }));
    await userEvent.click(screen.getByRole("checkbox", { name: /the exact characters sent/ }));
    expect(await screen.findByText(/PROMPT # Your job/)).toBeInTheDocument();
  });
});
