import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { WorkerActivity } from "../api";
import { installServer } from "../test/fakeServer";
import WorkerSessions from "./WorkerSessions";
import type { WorkerSession } from "./WorkerSessions";

const BASE = "/runs/output/acmesync-guide";
const SESSIONS: WorkerSession[] = [
  { key: "phase-4", label: "Phase 4 · document writing", readPath: `${BASE}/phases/4/worker`, isRunning: false },
  { key: "part-1", label: "Phase 7 · part 1", readPath: `${BASE}/document-pass/parts/1`, isRunning: false },
  { key: "part-2", label: "Phase 7 · part 2", readPath: `${BASE}/document-pass/parts/2`, isRunning: true },
];

function buildActivity(thinking: string, tool: string): WorkerActivity {
  return { steps: [], thinking: "", thinking_at: null, messages: [], turns: [
    { at: null, call: 1, thinking, text: "", steps: [{ at: null, call: 1, tool, args: '{"section": "K2"}', result: "K2 read" }] }] };
}

function serveSessions() {
  installServer({
    [`GET /api${BASE}/phases/4/worker`]: buildActivity("Phase 4 plans its chapters.", "read_notes"),
    [`GET /api${BASE}/document-pass/parts/1`]: buildActivity("Part 1 reads the outline.", "read_knowledge_outline"),
    [`GET /api${BASE}/document-pass/parts/2`]: buildActivity("Part 2 merges the router facts.", "read_section"),
  });
}

describe("WorkerSessions", () => {
  it("opens the running worker first and lists every session, the running one marked", async () => {
    serveSessions();
    render(<WorkerSessions sessions={SESSIONS} />);

    const list = screen.getByRole("combobox", { name: "worker session" });
    expect(list).toHaveValue("part-2");
    expect(screen.getByRole("option", { name: "Phase 7 · part 2 · running" })).toBeInTheDocument();
    expect(await screen.findByRole("list", { name: "turns of Phase 7 · part 2" })).toBeInTheDocument();
    expect(screen.getByText("Read section")).toBeInTheDocument();
  });

  it("shows the session chosen in the list, or by Previous and Next, as the live panel shows its turns", async () => {
    serveSessions();
    render(<WorkerSessions sessions={SESSIONS} />);
    await screen.findByRole("list", { name: "turns of Phase 7 · part 2" });

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "worker session" }), "part-1");
    expect(await screen.findByText("Read knowledge outline")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "previous session" }));
    expect(await screen.findByText("Read notes")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "previous session" })).toBeDisabled();
  });

  it("opens the session another card named, and the last one when no worker runs", async () => {
    serveSessions();
    const { unmount } = render(<WorkerSessions sessions={SESSIONS} chosenKey="part-1" />);
    expect(screen.getByRole("combobox", { name: "worker session" })).toHaveValue("part-1");
    unmount();

    const finished = SESSIONS.map((session) => ({ ...session, isRunning: false }));
    render(<WorkerSessions sessions={finished} />);
    expect(screen.getByRole("combobox", { name: "worker session" })).toHaveValue("part-2");
  });

  it("says so when no worker has run", () => {
    render(<WorkerSessions sessions={[]} />);
    expect(screen.getByText("No worker has run yet.")).toBeInTheDocument();
  });
});
