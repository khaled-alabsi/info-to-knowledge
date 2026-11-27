import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { answerText, installServer } from "../test/fakeServer";
import MarkdownView from "./MarkdownView";

// A page older than the last build asks for a Mermaid code file that build deleted: the import fails.
vi.mock("mermaid", () => {
  throw new Error("Failed to fetch dynamically imported module");
});

describe("MarkdownView with Mermaid missing", () => {
  it("says the diagram drawer did not load, keeps the source, and does not stop the rest", async () => {
    installServer({ "GET /api/runs/knowledge/AcmeSync/files/04_document.md":
      answerText("# Flow\n\n```mermaid\ngraph TD; Gateway-->AcmeSync\n```\n") });
    render(<MarkdownView runBase="/runs/knowledge/AcmeSync" path="04_document.md" />);
    expect(await screen.findByRole("heading", { name: "Flow" })).toBeInTheDocument();
    await vi.waitFor(() => expect(document.querySelector(".mermaid")).toHaveClass("mermaid-error"));
    expect(document.querySelector(".mermaid")?.textContent).toBe(
      "The diagram drawer did not load (the page is older than the last build): reload the page.\n\ngraph TD; Gateway-->AcmeSync\n");
  });
});
