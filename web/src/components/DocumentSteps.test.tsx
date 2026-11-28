import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { answerJson, installServer } from "../test/fakeServer";
import DocumentSteps from "./DocumentSteps";

const RUN_BASE = "/runs/output/Gateway-review";

describe("DocumentSteps", () => {
  it("lists phase 4's steps by stage, with status, calls, time, tokens and each refused answer with why", async () => {
    installServer({
      [`GET /api${RUN_BASE}/document-steps`]: [
        { slug: "executive-summary-draft", stage: "table of contents", step: "executive summary draft", status: "accepted",
          calls: 1, seconds: 120, prompt_tokens: 23000, output_tokens: 2500, rejections: [] },
        { slug: "parts", stage: "table of contents", step: "agenda", status: "accepted", calls: 2, seconds: 300,
          prompt_tokens: 60000, output_tokens: 5000,
          rejections: [{ findings: [{ code: "AGENDA_POINT_EMPTY", text: "point 2 holds nothing" }] }] },
        { slug: "chapter-01-text", stage: "chapters", step: "chapter 1 text", status: "waiting", calls: 0, seconds: 0,
          prompt_tokens: 0, output_tokens: 0, rejections: [] },
      ],
    });
    render(<DocumentSteps runBase={RUN_BASE} refreshKey="" />);
    const rows = (await screen.findAllByRole("row")).slice(1);
    expect(within(rows[0]).getByText("table of contents")).toBeInTheDocument();
    expect(within(rows[0]).getByText("23,000 / 2,500")).toBeInTheDocument();
    expect(within(rows[1]).queryByText("table of contents")).not.toBeInTheDocument();
    expect(within(rows[1]).getByText("AGENDA_POINT_EMPTY")).toBeInTheDocument();
    // the reason's name alone in the cell, its fix text kept for Copy (the owner: the long text is "too big and not needed")
    expect(within(rows[1]).queryByText(/point 2 holds nothing/)).not.toBeInTheDocument();
    expect(within(rows[1]).getByText("AGENDA_POINT_EMPTY")).toBeInTheDocument();
    expect(within(rows[2]).getByText("chapters")).toBeInTheDocument();
    expect(within(rows[2]).getByText("waiting")).toBeInTheDocument();
    expect(screen.getByText(/2 of 3 steps accepted/)).toHaveTextContent("1 rejection");
  });

  it("shows the steps while every answer lands after the next refresh has started defect", async () => {
    // A live run refreshed the table on every update, and each refresh threw away the answer still on its way: the
    // table loaded for ever. Now an answer is dropped only when a newer one is already shown.
    const held: (() => void)[] = [];
    installServer({ [`GET /api${RUN_BASE}/document-steps`]: () => new Promise((resolve) => held.push(() => resolve([
      { slug: "parts", stage: "table of contents", step: "agenda", status: "accepted", calls: 1, seconds: 60,
        prompt_tokens: 1000, output_tokens: 100, rejections: [] }]))) });
    const { rerender } = render(<DocumentSteps runBase={RUN_BASE} refreshKey="1" />);
    await vi.waitFor(() => expect(held).toHaveLength(1));
    rerender(<DocumentSteps runBase={RUN_BASE} refreshKey="2" />);
    await vi.waitFor(() => expect(held).toHaveLength(2));
    held[0]();
    expect(await screen.findByText("agenda")).toBeInTheDocument();
    held[1]();
  });

  it("says when no step was asked, and shows a failed read", async () => {
    installServer({ [`GET /api${RUN_BASE}/document-steps`]: [] });
    const { unmount } = render(<DocumentSteps runBase={RUN_BASE} refreshKey="" />);
    expect(await screen.findByText("No step has been asked yet.")).toBeInTheDocument();
    unmount();
    installServer({ [`GET /api${RUN_BASE}/document-steps`]: answerJson({ detail: "no such run" }, 404) });
    render(<DocumentSteps runBase={RUN_BASE} refreshKey="" />);
    expect(await screen.findByText(/no such run/)).toHaveClass("error");
  });

  it("copies the table as markdown, each step a row and its rejections in the last cell", async () => {
    const user = userEvent.setup();
    installServer({ [`GET /api${RUN_BASE}/document-steps`]: [
      { slug: "parts", stage: "table of contents", step: "agenda", status: "accepted", calls: 2, seconds: 300,
        prompt_tokens: 60000, output_tokens: 5000, rejections: [{ findings: [{ code: "AGENDA_POINT_EMPTY", text: "point 2 holds nothing" }] }] }] });
    render(<DocumentSteps runBase={RUN_BASE} refreshKey="" />);
    await user.click(await screen.findByRole("button", { name: "Copy" }));
    const copied = await navigator.clipboard.readText();
    expect(copied.split("\n")[0]).toBe("| Stage | Step | Status | Calls | Took | Tokens in / out | Rejections (1) |");
    expect(copied).toContain("| table of contents | agenda | accepted | 2 | 5m 00s | 60,000 / 5,000 | 1: AGENDA_POINT_EMPTY point 2 holds nothing |");
  });

  it("opens the attempt a rejected row names", async () => {
    const onOpenStep = vi.fn();
    installServer({ [`GET /api${RUN_BASE}/document-steps`]: [
      { slug: "parts", stage: "table of contents", step: "agenda", status: "accepted", calls: 2, seconds: 300,
        prompt_tokens: 60000, output_tokens: 5000, rejections: [{ findings: [{ code: "AGENDA_POINT_EMPTY", text: "point 2 holds nothing" }] }] }] });
    render(<DocumentSteps runBase={RUN_BASE} refreshKey="" onOpenStep={onOpenStep} />);
    await userEvent.click(await screen.findByRole("button", { name: "see what the worker saw for agenda" }));
    expect(onOpenStep).toHaveBeenCalledWith("parts");
  });
});
