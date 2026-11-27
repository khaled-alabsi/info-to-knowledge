import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { answerJson, installServer } from "../test/fakeServer";
import type { Phase } from "../api";
import WorkerView from "./WorkerView";

const RUN_BASE = "/runs/output/Gateway-review";
const PHASE_3: Phase = { number: 3, name: "frames_processing", title: "Frames processing" } as Phase;
const PHASE_4: Phase = { number: 4, name: "finalize", title: "Document writing" } as Phase;
const BATCH_1 = "logs/s1/frames_processing/frames-000-001/attempt-1";
const BATCH_2 = "logs/s1/frames_processing/frames-000-001/attempt-2";

function installPhase3() {
  return installServer({
    [`GET /api${RUN_BASE}/worker?phase=3`]: { steps: [
      { step: "frames-000-001", kind: "call", is_dry_run: false, attempts: [
        { ref: BATCH_1, label: "attempt 1", session: "s1", status: "refused" },
        { ref: BATCH_2, label: "attempt 2", session: "s1", status: "accepted" }] },
      { step: "frames-000-001-critique", kind: "call", is_dry_run: false, attempts: [
        { ref: "logs/s1/frames_processing/frames-000-001-critique/attempt-1", label: "attempt 1", session: "s1", status: "accepted" }] },
    ] },
    [`GET /api${RUN_BASE}/worker/attempt?ref=${encodeURIComponent(BATCH_2)}`]: {
      kind: "call", instructions: "# Your job\n\nYou write notes for each frame.", findings: [], turns: [],
      input: [{ kind: "text", text: "Frame 0, captured at 1.0 s." }, { kind: "image", frame: 0, path: "frames/frame-000.jpg" },
        { kind: "image", frame: null, path: null }] },
    [`GET /api${RUN_BASE}/worker/attempt?ref=${encodeURIComponent(BATCH_1)}`]: {
      kind: "call", instructions: "You write notes for each frame.", turns: [], input: [],
      findings: [{ code: "FRAME_FACTS_EMPTY", detail: "frame 1 has no facts" }] },
    "GET /api/phase-instructions?phase=3&audio=false": { instructions: [
      { name: "frames_processing", used_by: "Phase 3, the first call of each batch.", text: "FRAMES INSTRUCTIONS", error: "" },
      { name: "critique", used_by: "Phase 3, after each batch.", text: "", error: "the critique needs a batch" }] },
  });
}

describe("WorkerView", () => {
  it("lists a phase's steps and opens the newest attempt of the first one on its instructions, then its input with its frames", async () => {
    const user = userEvent.setup();
    installPhase3();
    render(<WorkerView runBase={RUN_BASE} phase={PHASE_3} isAudio={false} onClose={() => {}} />);
    const steps = await screen.findByRole("list", { name: "steps" });
    expect(within(steps).getAllByRole("button").map((button) => button.textContent))
      .toEqual(["0–1 · readingaccepted after 1 rejected", "0–1 · critiqueaccepted"]);
    expect(await screen.findByText("You write notes for each frame.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Your job" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Raw text" }));
    expect(screen.getByText(/# Your job\s+You write notes for each frame\./)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Your job" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Raw text" }));
    await user.click(screen.getByRole("tab", { name: "Input" }));
    expect(screen.getByText("Frame 0, captured at 1.0 s.")).toBeInTheDocument();
    const frame = screen.getByRole("img", { name: "frame 0" });
    expect(frame).toHaveAttribute("src", `/api${RUN_BASE}/files/frames/frame-000.jpg`);
    expect(screen.getByText("an image whose frame the text before it does not name")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "enlarge frame 0" }));
    expect(frame.closest("figure")).toHaveClass("enlarged");
    await user.click(screen.getByRole("button", { name: "make frame 0 small again" }));
    expect(frame.closest("figure")).not.toHaveClass("enlarged");
  });

  it("opens on a named step, and lists only the steps with a rejection when asked", async () => {
    installPhase3();
    render(<WorkerView runBase={RUN_BASE} phase={PHASE_3} isAudio={false} onClose={() => {}} initialStep="frames-000-001-critique" />);
    const steps = await screen.findByRole("list", { name: "steps" });
    expect(within(steps).getByRole("button", { name: /critique/ })).toHaveAttribute("aria-current", "true");
    await userEvent.click(screen.getByRole("checkbox", { name: /Only steps with a rejection/ }));
    expect(within(screen.getByRole("list", { name: "steps" })).getAllByRole("button").map((button) => button.textContent))
      .toEqual(["0–1 · readingaccepted after 1 rejected"]);
  });

  it("copies what the open tab shows", async () => {
    const user = userEvent.setup();
    installPhase3();
    render(<WorkerView runBase={RUN_BASE} phase={PHASE_3} isAudio={false} onClose={() => {}} />);
    expect(await screen.findByText("You write notes for each frame.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Copy" }));
    expect(await navigator.clipboard.readText()).toContain("You write notes for each frame.");
  });

  it("drops an attempt's load error once another attempt loads", async () => {
    const user = userEvent.setup();
    installServer({
      [`GET /api${RUN_BASE}/worker?phase=3`]: { steps: [{ step: "frames-000-001", kind: "call", is_dry_run: false, attempts: [
        { ref: BATCH_1, label: "attempt 1", session: "s1", status: "refused" },
        { ref: BATCH_2, label: "attempt 2", session: "s1", status: "accepted" }] }] },
      [`GET /api${RUN_BASE}/worker/attempt?ref=${encodeURIComponent(BATCH_1)}`]: answerJson({ detail: "the attempt's files are gone" }, 404),
      [`GET /api${RUN_BASE}/worker/attempt?ref=${encodeURIComponent(BATCH_2)}`]: {
        kind: "call", instructions: "You write notes for each frame.", findings: [], turns: [], input: [] },
      "GET /api/phase-instructions?phase=3&audio=false": { instructions: [] },
    });
    render(<WorkerView runBase={RUN_BASE} phase={PHASE_3} isAudio={false} onClose={() => {}} />);
    await user.click(await screen.findByRole("button", { name: "attempt 1 · refused" }));
    expect(await screen.findByText("404: the attempt's files are gone")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "attempt 2 · accepted" }));
    expect(await screen.findByText("You write notes for each frame.")).toBeInTheDocument();
    expect(screen.queryByText("404: the attempt's files are gone")).not.toBeInTheDocument();
  });

  it("switches to an earlier attempt and shows why it was refused", async () => {
    const user = userEvent.setup();
    installPhase3();
    render(<WorkerView runBase={RUN_BASE} phase={PHASE_3} isAudio={false} onClose={() => {}} />);
    await user.click(await screen.findByRole("button", { name: "attempt 1 · refused" }));
    const findings = await screen.findByLabelText("why this attempt was rejected");
    expect(findings).toHaveTextContent("FRAME_FACTS_EMPTY frame 1 has no facts");
  });

  it("previews each instruction set of the phase from the current files, and says why one cannot be built", async () => {
    const user = userEvent.setup();
    installPhase3();
    render(<WorkerView runBase={RUN_BASE} phase={PHASE_3} isAudio={false} onClose={() => {}} />);
    await user.click(screen.getByRole("button", { name: /Preview instructions/ }));
    expect(await screen.findByText("FRAMES INSTRUCTIONS")).toBeInTheDocument();
    expect(screen.getByText("Phase 3, the first call of each batch.")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "critique" }));
    expect(screen.getByText("the critique needs a batch")).toBeInTheDocument();
  });

  it("shows a conversation's follow-up with its request and its turns in order", async () => {
    const user = userEvent.setup();
    installServer({
      [`GET /api${RUN_BASE}/worker?phase=4`]: { steps: [{ step: "chapter-01-text", kind: "conversation", is_dry_run: false,
        attempts: [{ ref: "conversation:4:chapter-01-text", label: "conversation", session: "", status: "accepted" }] }] },
      [`GET /api${RUN_BASE}/worker/attempt?ref=${encodeURIComponent("conversation:4:chapter-01-text")}`]: {
        kind: "conversation", instructions: null, findings: [], input: [{ kind: "text", text: "Write chapter 1." }],
        turns: [{ kind: "turn", thinking: "read first", said: "", calls: [{ name: "read_notes", args: { chapter: 1 } }],
          prompt_tokens: 1200 },
        { kind: "result", tool: "read_notes", result: "Chapter 1 read whole." },
        { kind: "accepted", text: "accepted after 0 refused answer(s)" }] },
    });
    render(<WorkerView runBase={RUN_BASE} phase={PHASE_4} isAudio={false} onClose={() => {}} />);
    expect(await screen.findByText(/This run did not save the instructions/)).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Input" }));
    expect(screen.getByText("Write chapter 1.")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Turns" }));
    expect(screen.getByText("worker · 1,200 tokens in")).toBeInTheDocument();
    expect(screen.getByText(/read_notes\(/)).toBeInTheDocument();
    expect(screen.getByText("Chapter 1 read whole.")).toBeInTheDocument();
    expect(screen.getByText("accepted after 0 rejection(s)")).toBeInTheDocument();
  });

  it("says when the run saved no call of the phase", async () => {
    installServer({ [`GET /api${RUN_BASE}/worker?phase=3`]: { steps: [] } });
    render(<WorkerView runBase={RUN_BASE} phase={PHASE_3} isAudio={false} onClose={() => {}} />);
    expect(await screen.findByText(/Nothing to show yet: this phase has not called its model/)).toBeInTheDocument();
  });
});
