import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { WorkerActivity } from "../api";
import { installServer } from "../test/fakeServer";
import WorkerLive from "./WorkerLive";

const READ = "/runs/output/acmesync-guide/parts/3/live";
const SEND = "/runs/output/acmesync-guide/parts/3/messages";

function buildActivity(change: Partial<WorkerActivity>): WorkerActivity {
  return { steps: [], thinking: "", thinking_at: null, messages: [], ...change };
}

function openPanel(activity: WorkerActivity, isRunning: boolean, canAsk = true, document?: string) {
  installServer({ [`GET /api${READ}`]: activity });
  render(<WorkerLive title="Part 3" label="part 3" runBase="/runs/output/acmesync-guide" readPath={READ} sendPath={SEND}
    isRunning={isRunning} canAsk={canAsk} document={document} onClose={() => undefined} />);
}

describe("WorkerLive", () => {
  it("shows each saved turn with its thinking, its words and each tool call by icon, name and inputs", async () => {
    openPanel(buildActivity({ turns: [
      { at: 1_790_000_000, call: 1, thinking: "I read the refund rules first.", text: "The section **holds** two rules.",
        steps: [{ at: 1_790_000_000, call: 1, tool: "read_section", summary: "K2", args: '{"section": "K2", "blocks": [1, 2]}',
          result: "K2 read whole.\nRefund limits\nRetry budget" },
        { at: 1_790_000_001, call: 1, tool: "edit_blocks", args: '{"edits": [{"block": 3, "action": "remove"}]}',
          result: "Refused: block 9 is not in this part" },
        { at: 1_790_000_002, call: 1, tool: "submit_answer", args: "{\"cut", result: "" },
        { at: 1_790_000_003, call: 1, tool: "report_problem", args: "", result: "recorded" },
        { at: 1_790_000_004, call: 1, tool: "count_words", args: "[1]", result: "" }] }] }), false);

    expect(await screen.findByRole("list", { name: "turns of part 3" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Turns (1)" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Thinking · 6 words")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "worker output" })).toHaveTextContent("The section holds two rules.");
    expect(screen.getByText("Read section")).toBeInTheDocument();
    // the inputs as name and value, a list one item a line, never raw JSON
    expect(screen.getByText("section")).toBeInTheDocument();
    expect(screen.queryByText(/\{"section"/)).not.toBeInTheDocument();
    expect(screen.getByText("→ K2 read whole.")).toBeInTheDocument();
    expect(screen.getByText("Refused: Refused: block 9 is not in this part")).toBeInTheDocument();
    // inputs the server cut short, or no list of named inputs, stay as text, folded
    expect(screen.getAllByText("inputs")).toHaveLength(2);
    expect(screen.getByText("Report problem")).toBeInTheDocument();
    // no chat tab once the worker is done and nobody wrote to it
    expect(screen.queryByRole("tab", { name: /^Chat/ })).not.toBeInTheDocument();
  });

  it("puts a worker's newest thinking on its last step when it saved no turns, and says when there is none", async () => {
    openPanel(buildActivity({ steps: [
      { at: "2026-11-09T21:00:00", call: 1, tool: "look", args: '{"frame": 4}', result: "frame 4" },
      { at: "2026-11-09T21:00:05", call: 2, tool: "cut", args: '{"frame": 4}', result: "cut-01" }],
    thinking: "The diagram needs its legend." }), false);

    expect(await screen.findByRole("tab", { name: "Turns (2)" })).toBeInTheDocument();
    expect(screen.getAllByText(/^Thinking · /)).toHaveLength(1);
    expect(screen.getByText("Thinking · 5 words")).toBeInTheDocument();
  });

  it("shows the call under way as the next turn, open, and the chat with its two ways to write", async () => {
    openPanel(buildActivity({ steps: [], thinking: "Checking the second table now.", is_worker_running: true,
      doing: { what: "thinking", since: 1_790_000_000 }, speed: { tokens_per_second: 41, is_estimated: true,
        prompt_tokens: 12000, is_writing: true } }), true, true, "07_reshaping/parts/part-03.md");

    expect(await screen.findByText("Now: thinking")).toBeInTheDocument();
    expect(screen.getByText(/Writing at 41 tokens a second/)).toBeInTheDocument();
    expect(screen.getByText("Thinking · 5 words so far")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Document so far" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: "Chat" }));
    expect(screen.getByRole("button", { name: "Ask a question" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "Send a message" }));
    expect(screen.getByRole("button", { name: "Send a message" })).toHaveAttribute("aria-pressed", "true");
  });

  it("lists the turns newest first, marks the turn that took a message, and shows a message as a note with no reply", async () => {
    const turn = (call: number, at: number) => ({ at, call, thinking: "", text: `Wrote part ${call}.`, steps: [] });
    openPanel(buildActivity({ turns: [turn(1, 1_790_000_000), turn(2, 1_790_000_100), turn(3, 1_790_000_200)],
      messages: [{ at: 1_790_000_040, text: "Be faster.", role: "system", is_question: false, is_sent: true,
        sent_at: 1_790_000_050, answer: "" },
      { at: 1_790_000_150, text: "How far are you?", role: "user", is_question: true, is_sent: true, sent_at: 1_790_000_160,
        answer: "Half of it." }] }), false);

    const turns = await screen.findByRole("list", { name: "turns of part 3" });
    const heads = [...turns.querySelectorAll(":scope > li .turn-head strong")].map((head) => head.textContent);
    expect(heads).toEqual(["Turn 3", "Turn 2", "Turn 1"]);
    // each message marks the first turn written after the worker took it
    const marks = [...turns.querySelectorAll(":scope > li")].map((row) => row.querySelector(".turn-messages")?.textContent ?? "");
    expect(marks[0]).toContain("Your question reached the worker here: How far are you?");
    expect(marks[1]).toContain("Your message as system reached the worker here: Be faster.");
    expect(marks[2]).toBe("");
    // Send a message shows only the messages, as notes that wait for no answer; Ask a question the chat alone
    await userEvent.click(screen.getByRole("tab", { name: /^Chat/ }));
    expect(screen.getByRole("list", { name: "messages to part 3" })).toHaveTextContent("Half of it.");
    expect(screen.getByRole("list", { name: "messages to part 3" })).not.toHaveTextContent("Be faster.");
    await userEvent.click(screen.getByRole("button", { name: "Send a message" }));
    const notes = screen.getByRole("list", { name: "messages to part 3" });
    expect(notes).toHaveTextContent("Message as system · the worker has itBe faster.");
    expect(notes).not.toHaveTextContent("How far are you?");
    expect(notes.querySelector(".chat-bubble")).toBeNull();
  });

  it("says no turn is saved yet, and that a refused read needs the server restarted", async () => {
    installServer({});
    render(<WorkerLive title="Part 3" label="part 3" runBase="/runs/output/acmesync-guide" readPath={READ} sendPath={SEND}
      isRunning={false} canAsk={false} onClose={() => undefined} />);
    expect(await screen.findByText("This needs the server restarted: it runs older code.")).toBeInTheDocument();
  });
});
