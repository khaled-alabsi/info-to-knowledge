import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { installServer } from "../test/fakeServer";
import BatchHistory from "./BatchHistory";

const RUN_BASE = "/runs/output/Gateway-review";

describe("BatchHistory", () => {
  it("lists every batch, the newest first and its steps in the order they ran, with its time, tokens, result and why an answer was refused", async () => {
    installServer({
      [`GET /api${RUN_BASE}/batches`]: [
        { slug: "frames-001-002", frames: "1–2", step: "reading", started_at: "2026-01-01T10:00:00", duration_s: 125,
          status: "2 of 2 accepted", attempts: 1, prompt_tokens: 26000, output_tokens: 5000, rejections: [] },
        { slug: "frames-001-002-critique", frames: "1–2", step: "critique", started_at: "2026-01-01T10:03:00", duration_s: 60,
          status: "3 mistakes found, in 2 of 2 frames", attempts: 2, prompt_tokens: 6000, output_tokens: 9000,
          rejections: [{ attempt: 1, findings: [{ code: "CRITIQUE_RULE_NOT_GENERIC", detail: "a rule quotes a screen" }] }] },
      ],
    });
    render(<BatchHistory runBase={RUN_BASE} refreshKey="" />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading…");
    const rows = (await screen.findAllByRole("row")).slice(1);
    expect(within(rows[0]).getByText("reading")).toBeInTheDocument();
    expect(within(rows[0]).getByText("26,000 / 5,000")).toBeInTheDocument();
    expect(within(rows[0]).getByText("0")).toBeInTheDocument();
    expect(within(rows[1]).getByText("critique")).toBeInTheDocument();
    expect(within(rows[1]).getByText("1 of 2 attempts")).toBeInTheDocument();
    expect(within(rows[1]).getByText("CRITIQUE_RULE_NOT_GENERIC")).toBeInTheDocument();
    expect(within(rows[1]).getByText(/a rule quotes a screen/)).toBeInTheDocument();
    expect(within(rows[1]).getByText("3 mistakes found, in 2 of 2 frames (2 attempts)")).toBeInTheDocument();
    expect(rows[0]).toHaveClass("batch-start");
    expect(rows[1]).not.toHaveClass("batch-start");
    expect(within(rows[1]).queryByText("1–2")).not.toBeInTheDocument();
    expect(screen.getByText(/2 steps · .* in all · 1 rejected at least once/)).toBeInTheDocument();
  });

  it("names how many rejections the table holds, and Copy copies it as a markdown table", async () => {
    const user = userEvent.setup();
    installServer({
      [`GET /api${RUN_BASE}/batches`]: [
        { slug: "frames-001-002-critique", frames: "1–2", step: "critique", started_at: "2026-01-01T10:03:00", duration_s: 60,
          status: "1 mistake found, in 1 of 2 frames", attempts: 3, prompt_tokens: 6000, output_tokens: 9000,
          rejections: [{ attempt: 1, findings: [{ code: "CRITIQUE_EMPTY", detail: "no rule" }] },
            { attempt: 1, findings: [{ code: "CRITIQUE_EMPTY", detail: "no rule | none" }] }] },
      ],
    });
    render(<BatchHistory runBase={RUN_BASE} refreshKey="" />);
    expect(await screen.findByRole("columnheader", { name: "Rejections (2)" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Copy" }));
    const copied = await navigator.clipboard.readText();
    expect(copied.split("\n")[0]).toBe("| Frames | Step | Took | Tokens in / out | Result | Rejections (2) |");
    expect(copied).toContain("| 1–2 | critique |");
    expect(copied).toContain("attempt 1: CRITIQUE_EMPTY no rule / attempt 1: CRITIQUE_EMPTY no rule \\| none |");
    expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();
  });
});
