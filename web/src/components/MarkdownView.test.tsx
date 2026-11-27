import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { answerJson, answerText, installServer } from "../test/fakeServer";
import MarkdownView, { cutAtBlockEnd } from "./MarkdownView";

// jsdom cannot lay out an SVG, so Mermaid and the code colourer are replaced; the page's own DOM around them is real.
const drawing = vi.hoisted(() => ({
  initialize: vi.fn(),
  run: vi.fn(async ({ nodes }: { nodes: HTMLElement[] }) => {
    const source = nodes[0].textContent ?? "";
    if (source.includes("broken")) throw new Error("Parse error on line 2");
    nodes[0].innerHTML = source.includes("unsized")
      ? '<svg><g></g></svg>'
      : '<svg viewBox="0 0 400 200"><g><text>Gateway</text></g></svg>';
  }),
}));
const codeColourer = vi.hoisted(() => ({ highlightElement: vi.fn((code: HTMLElement) => code.classList.add("hljs")) }));
vi.mock("mermaid", () => ({ default: drawing }));
vi.mock("highlight.js/lib/common", () => ({ default: codeColourer }));

const RUN_BASE = "/runs/knowledge/AcmeSync-demo";
const PATH = "AcmeSync-demo/AcmeSync-demo.md";
const FILE_URL = `/api${RUN_BASE}/files/AcmeSync-demo/AcmeSync-demo.md`;

const DOCUMENT = `---
title: "AcmeSync rollout"
presenter: Jonas
speakers: ["Jonas", "Mira"]
listeners: []
empty:
not a field line
---
# Contents

- [The agenda](#agenda)
- [Gateway flow](#gateway-flow)
- [Second agenda](#agenda-1)
- [Guide](guide.md)
- [Site](https://example.com/acme)

## Agenda

![The Gateway screen](resources/figure-01.png)

[![linked figure](resources/figure-02.png)](resources/figure-02.png)

![remote](https://example.com/remote.png)

## Gateway flow

\`\`\`mermaid
graph TD; A-->B
\`\`\`

\`\`\`mermaid
graph TD; broken
\`\`\`

\`\`\`mermaid
graph TD; unsized
\`\`\`

\`\`\`python
print("AcmeSync")
\`\`\`

## Agenda
`;

function serveDocument(text = DOCUMENT) {
  return installServer({ [`GET ${FILE_URL}`]: answerText(text) });
}

async function renderDocument() {
  const view = render(<MarkdownView runBase={RUN_BASE} path={PATH} />);
  await screen.findByRole("heading", { name: "Contents" });
  return view;
}

describe("MarkdownView", () => {
  it("says while it loads and when the file cannot be read", async () => {
    installServer({ [`GET ${FILE_URL}`]: answerText("", 404) });
    render(<MarkdownView runBase={RUN_BASE} path={PATH} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(await screen.findByText(`could not read ${PATH}: 404`)).toHaveClass("error");
  });

  it("shows the front matter as a table, and the tools for the file", async () => {
    serveDocument();
    await renderDocument();
    const table = document.querySelector<HTMLElement>(".front-matter")!;
    expect([...table.querySelectorAll("tr")].map((row) => row.textContent)).toEqual(["titleAcmeSync rollout", "presenterJonas", "speakersJonas, Mira",
      "listeners—", "empty—"]);
    expect(screen.getByText(`${PATH} · ${(DOCUMENT.length / 1000).toFixed(1)}k characters`)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open" })).toHaveAttribute("href", FILE_URL);
    expect(screen.getByRole("link", { name: /Download/ })).toHaveAttribute("href", `/api${RUN_BASE}/download/AcmeSync-demo/AcmeSync-demo.md`);
  });

  it("an earlier version kept in phase 7's folder shows its figures from the published folder", async () => {
    const versionPath = "07_reshaping/before.md";
    installServer({ [`GET /api${RUN_BASE}/files/${versionPath}`]: answerText("# Contents\n\n![The Gateway screen](resources/figure-01.png)\n") });
    render(<MarkdownView runBase={RUN_BASE} path={versionPath} linkFolder="AcmeSync-demo/" />);
    expect(await screen.findByRole("img", { name: "The Gateway screen" }))
      .toHaveAttribute("src", `/api${RUN_BASE}/files/AcmeSync-demo/resources/figure-01.png`);
  });

  it("draws a large document a part at a time, each Show more adding a part, and the editor the whole defect", async () => {
    // A 6 MB document drawn whole ran the owner's phone out of memory, and Safari reloaded the page again and again.
    // A large document is drawn in parts of about 300,000 characters, cut where a block ends
    const paragraph = `${"The gateway routes the tenant to its shard. ".repeat(20)}\n\n`;
    const large = `# Gateway\n\n${paragraph.repeat(800)}## Last section\n\nThe end.\n`;
    installServer({ [`GET ${FILE_URL}`]: answerText(large) });
    render(<MarkdownView runBase={RUN_BASE} path={PATH} />);
    const more = await screen.findByRole("button", { name: "Show more" });
    expect(screen.getByText(/^Shown: \d+% of the document/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Last section" })).not.toBeInTheDocument();
    await userEvent.click(more);
    await userEvent.click(await screen.findByRole("button", { name: "Show more" }));
    expect(await screen.findByRole("heading", { name: "Last section" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show more" })).not.toBeInTheDocument();
  }, 30_000);

  it("cuts a document's start where a block ends, never inside one", () => {
    expect(cutAtBlockEnd("one\n\ntwo two\n\nthree", 12)).toBe("one\n\ntwo two");
    expect(cutAtBlockEnd("short", 12)).toBe("short");
  });

  it("renders a document with no front matter and no code without loading the drawers", async () => {
    serveDocument("# Contents\n\nplain text");
    await renderDocument();
    expect(document.querySelector(".front-matter")).not.toBeInTheDocument();
    expect(drawing.initialize).not.toHaveBeenCalled();
    expect(codeColourer.highlightElement).not.toHaveBeenCalled();
  });

  it("gives headings GitHub's anchors and scrolls the document in place on a # link", async () => {
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    serveDocument();
    await renderDocument();
    const headings = screen.getAllByRole("heading", { level: 2 });
    expect(headings.map((heading) => heading.id)).toEqual(["agenda", "gateway-flow", "agenda-1"]);
    await userEvent.click(screen.getByRole("link", { name: "Gateway flow" }));
    expect(scroll).toHaveBeenCalledTimes(1);
    expect(scroll.mock.contexts[0]).toBe(headings[1]);
    expect(scroll).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
    expect(window.location.hash).toBe("");
    await userEvent.click(screen.getByRole("link", { name: "Second agenda" }));
    expect(scroll.mock.contexts[1]).toBe(headings[2]);
  });

  it("points relative images and links next to the document and opens other links in a new tab", async () => {
    serveDocument();
    await renderDocument();
    expect(screen.getByAltText("The Gateway screen")).toHaveAttribute("src", `/api${RUN_BASE}/files/AcmeSync-demo/resources/figure-01.png`);
    expect(screen.getByAltText("The Gateway screen")).toHaveAttribute("loading", "lazy");
    expect(screen.getByAltText("remote")).toHaveAttribute("src", "https://example.com/remote.png");
    const guide = screen.getByRole("link", { name: "Guide" });
    expect(guide).toHaveAttribute("href", `/api${RUN_BASE}/files/AcmeSync-demo/guide.md`);
    expect(guide).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: "Site" })).toHaveAttribute("href", "https://example.com/acme");
    expect(screen.getByRole("link", { name: "The agenda" })).not.toHaveAttribute("target");
  });

  it("draws each Mermaid diagram in place and says which could not be drawn, with its source", async () => {
    serveDocument();
    await renderDocument();
    await vi.waitFor(() => expect(drawing.run).toHaveBeenCalledTimes(3));
    expect(drawing.initialize).toHaveBeenCalledWith({ startOnLoad: false, securityLevel: "strict", theme: "default" });
    const boxes = document.querySelectorAll<HTMLElement>(".figure.diagram .mermaid");
    expect(boxes).toHaveLength(3);
    expect(boxes[0].querySelector("svg")).toBeInTheDocument();
    expect(boxes[1]).toHaveClass("mermaid-error");
    expect(boxes[1].textContent).toBe("This diagram could not be drawn: Parse error on line 2\n\ngraph TD; broken\n");
    expect(document.querySelectorAll(".figure-expand")).toHaveLength(3);
  });

  it("colours the code blocks, not the diagrams, and Copy copies the code", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    serveDocument();
    await renderDocument();
    const copy = await screen.findByRole("button", { name: "Copy" });
    expect(codeColourer.highlightElement).toHaveBeenCalledTimes(1);
    expect(codeColourer.highlightElement.mock.calls[0][0]).toHaveClass("language-python", "hljs");
    expect(copy.closest("pre")).toHaveClass("code-block");
    await user.click(copy);
    expect(await navigator.clipboard.readText()).toBe('print("AcmeSync")\n');
    expect(copy).toHaveTextContent("Copied");
    act(() => vi.advanceTimersByTime(1500));
    expect(copy).toHaveTextContent("Copy");
  });

  it("Expand opens a drawn diagram full screen at its own size, and Close leaves", async () => {
    serveDocument();
    await renderDocument();
    await vi.waitFor(() => expect(drawing.run).toHaveBeenCalledTimes(3));
    const expands = screen.getAllByRole("button", { name: "⤢ Expand" });
    await userEvent.click(expands[1]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(expands[0]);
    const viewer = screen.getByRole("dialog", { name: `${PATH} · diagram 1` });
    const svg = viewer.querySelector(".viewer-layer svg")!;
    expect(svg).toHaveAttribute("width", "400");
    expect(svg).toHaveAttribute("height", "200");
    expect(svg).toHaveAttribute("xmlns", "http://www.w3.org/2000/svg");
    await userEvent.click(within(viewer).getByRole("button", { name: /Close/ }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(expands[2]);
    const unsized = screen.getByRole("dialog", { name: `${PATH} · diagram 3` }).querySelector(".viewer-layer svg")!;
    expect(unsized).toHaveAttribute("width", "800");
    expect(unsized).toHaveAttribute("height", "600");
  });

  it("a click on a figure opens it full screen, unless the figure is a link", async () => {
    serveDocument();
    await renderDocument();
    fireEvent.click(screen.getByAltText("linked figure"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await userEvent.click(screen.getByAltText("The Gateway screen"));
    const viewer = screen.getByRole("dialog", { name: "The Gateway screen" });
    expect(within(viewer).getByRole("link", { name: /Save/ })).toHaveAttribute("download", "figure-01.png");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("a click on plain text does nothing", async () => {
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    serveDocument("# Contents\n\nplain text");
    await renderDocument();
    await userEvent.click(screen.getByText("plain text"));
    expect(scroll).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("Source shows the markdown and Rendered draws it again", async () => {
    serveDocument();
    await renderDocument();
    await userEvent.click(screen.getByRole("button", { name: "Source" }));
    expect(document.querySelector("pre.source")?.textContent).toBe(DOCUMENT);
    expect(screen.queryByRole("heading", { name: "Contents" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Rendered" }));
    expect(screen.getByRole("heading", { name: "Contents" })).toBeInTheDocument();
    await vi.waitFor(() => expect(drawing.run).toHaveBeenCalledTimes(6));
  });

  it("Read shows the document full screen, and Esc or Close leaves", async () => {
    serveDocument("# Contents");
    await renderDocument();
    const wrap = document.querySelector(".markdown-wrap")!;
    await userEvent.click(screen.getByRole("button", { name: "Full screen" }));
    expect(wrap).toHaveClass("reading");
    expect(document.body).toHaveClass("no-scroll");
    fireEvent.keyDown(window, { key: "Enter" });
    expect(wrap).toHaveClass("reading");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(wrap).not.toHaveClass("reading");
    expect(document.body).not.toHaveClass("no-scroll");
    await userEvent.click(screen.getByRole("button", { name: "Full screen" }));
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(wrap).not.toHaveClass("reading");
  });

  it("Full screen pins the document's contents on the left and leaves them out of the text, and a click scrolls to the heading", async () => {
    const scroll = vi.spyOn(Element.prototype, "scrollIntoView");
    serveDocument("## Contents\n\n- [Gateway flow](#gateway-flow)\n  - [Token step](#token-step)\n\n## Gateway flow\n\n### Token step\n\ntext");
    render(<MarkdownView runBase={RUN_BASE} path={PATH} />);
    await screen.findByRole("heading", { name: "Gateway flow" });
    expect(screen.queryByRole("navigation", { name: "contents" })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Full screen" }));
    const pinned = screen.getByRole("navigation", { name: "contents" });
    expect(within(pinned).getByRole("link", { name: "Token step" })).toHaveAttribute("href", "#token-step");
    const text = document.querySelector("article.markdown")!;
    expect(within(text as HTMLElement).queryByRole("heading", { name: "Contents" })).toBeNull();
    expect(within(text as HTMLElement).queryByRole("link", { name: "Gateway flow" })).toBeNull();
    await userEvent.click(within(pinned).getByRole("link", { name: "Token step" }));
    expect(scroll.mock.contexts.at(-1)).toBe(document.getElementById("token-step"));
    await userEvent.click(within(pinned).getByText("Contents", { selector: "strong" }));
    expect(scroll).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("navigation", { name: "contents" })).toBeNull();
    expect(screen.getByRole("heading", { name: "Contents" })).toBeInTheDocument();
  });

  it("Full screen pins nothing when the heading named Contents has no list after it", async () => {
    serveDocument("# Contents\n\nplain text");
    await renderDocument();
    await userEvent.click(screen.getByRole("button", { name: "Full screen" }));
    expect(screen.queryByRole("navigation", { name: "contents" })).toBeNull();
    expect(screen.getByRole("heading", { name: "Contents" })).toBeInTheDocument();
  });
});

// Editing a document in the page, the way the wiki UI does it: behind a lock, in the rendered text, one block at a
// time, with every version the save replaces kept by the server.
describe("MarkdownView editing", () => {
  const SHORT = "---\ntitle: AcmeSync rollout\n---\n# Gateway\n\nThe proxy sends the token.\n\nsecond paragraph\n";
  const VERSIONS_URL = `/api${RUN_BASE}/versions/AcmeSync-demo/AcmeSync-demo.md`;
  const VERSION_URL = `/api${RUN_BASE}/version/AcmeSync-demo/AcmeSync-demo.md`;

  async function openEditor(routes: Record<string, unknown> = {}) {
    // A Response's body is read once, and locking reads the file again, so the route answers with a fresh one.
    const calls = installServer({ [`GET ${FILE_URL}`]: () => answerText(SHORT), ...routes } as never);
    render(<MarkdownView runBase={RUN_BASE} path={PATH} />);
    await screen.findByRole("heading", { name: "Gateway" });
    await userEvent.click(screen.getByRole("button", { name: "Full screen" }));
    await userEvent.click(screen.getByRole("button", { name: "Edit" }));
    return calls;
  }

  function findBlock(text: string): HTMLElement {
    return screen.getByText(text).closest("[data-line]") as HTMLElement;
  }

  it("offers the lock only in reading mode", async () => {
    serveDocument(SHORT);
    render(<MarkdownView runBase={RUN_BASE} path={PATH} />);
    await screen.findByRole("heading", { name: "Gateway" });
    expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Full screen" }));
    expect(screen.getByRole("button", { name: /Edit/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save/ })).toBeNull();
  });

  it("shows one block's markdown under the caret, draws it again on leaving, and saves the whole document", async () => {
    const calls = await openEditor({ [`PUT ${FILE_URL}`]: { path: PATH, size_bytes: 120, kept_as: "20260101T101500.000000" } });
    await userEvent.click(findBlock("The proxy sends the token."));
    const area = document.querySelector<HTMLTextAreaElement>("textarea.block-source")!;
    expect(area.value).toBe("The proxy sends the token.");
    fireEvent.change(area, { target: { value: "The proxy sends the **token** to the provider." } });
    fireEvent.blur(area);
    expect(await screen.findByText("token")).toHaveProperty("tagName", "STRONG");
    expect(document.querySelector("textarea.block-source")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    const put = calls.find((call) => call.method === "PUT")!;
    expect(put.url).toBe(FILE_URL);
    expect((put.body as { markdown: string }).markdown).toBe(
      "---\ntitle: AcmeSync rollout\n---\n# Gateway\n\nThe proxy sends the **token** to the provider.\n\nsecond paragraph\n");
    expect(await screen.findByRole("status")).toHaveTextContent("the version it replaced is kept as");
  });

  it("puts the block back on undo and says when a save was refused", async () => {
    await openEditor({ [`PUT ${FILE_URL}`]: answerJson({ detail: "04_document.md is no markdown file" }, 404) });
    await userEvent.click(findBlock("second paragraph"));
    const area = document.querySelector<HTMLTextAreaElement>("textarea.block-source")!;
    fireEvent.change(area, { target: { value: "a sentence of the writer's own" } });
    fireEvent.blur(area);
    expect(await screen.findByText("a sentence of the writer's own")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "z", metaKey: true });
    expect(await screen.findByText("second paragraph")).toBeInTheDocument();
    expect(screen.queryByText("a sentence of the writer's own")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    expect(await screen.findByRole("status")).toHaveTextContent("not saved: 404: 04_document.md is no markdown file");
  });

  it("edits the front matter in a textarea of its own and saves it with the text", async () => {
    const calls = await openEditor({ [`PUT ${FILE_URL}`]: { path: PATH, size_bytes: 120, kept_as: "20260101T101500.000000" } });
    expect(document.querySelector(".front-matter")).toBeNull();
    const matter = screen.getByRole("textbox", { name: "front matter" }) as HTMLTextAreaElement;
    expect(matter.value).toBe("---\ntitle: AcmeSync rollout\n---\n");
    fireEvent.change(matter, { target: { value: "---\ntitle: AcmeSync rollout\nspeakers: Jonas\n---\n" } });
    await userEvent.click(screen.getByRole("button", { name: /Save/ }));
    const put = calls.find((call) => call.method === "PUT")!;
    expect((put.body as { markdown: string }).markdown).toBe(
      "---\ntitle: AcmeSync rollout\nspeakers: Jonas\n---\n# Gateway\n\nThe proxy sends the token.\n\nsecond paragraph\n");
  });

  it("locks the editor when reading mode is left, and keeps it open when the unsaved changes are kept", async () => {
    const asked = vi.spyOn(window, "confirm").mockReturnValue(false);
    await openEditor();
    await userEvent.click(findBlock("second paragraph"));
    fireEvent.change(document.querySelector("textarea.block-source")!, { target: { value: "an edit nobody saved" } });
    fireEvent.blur(document.querySelector("textarea.block-source")!);
    await screen.findByText("an edit nobody saved");
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(asked).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Close" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "front matter" })).toBeInTheDocument();
    asked.mockReturnValue(true);
    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(await screen.findByText("second paragraph")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Full screen" })).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "front matter" })).toBeNull();
    expect(document.querySelector(".front-matter")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save/ })).toBeNull();
    asked.mockRestore();
  });

  it("edits the whole file in Source mode while the lock is open", async () => {
    await openEditor();
    await userEvent.click(screen.getByRole("button", { name: "Source" }));
    const area = document.querySelector<HTMLTextAreaElement>("textarea.source")!;
    expect(area.value).toBe(SHORT);
    expect(screen.getByRole("button", { name: /Save/ })).toBeDisabled();
  });

  it("lists the versions the folder kept and puts one in the editor", async () => {
    await openEditor({
      [`GET ${VERSIONS_URL}`]: [{ at: "20260101T101500.000000", size_bytes: 2048 }],
      [`GET ${VERSION_URL}?at=20260101T101500.000000`]: { at: "20260101T101500.000000", markdown: "# Gateway\n\nthe earlier text\n" },
    });
    await userEvent.click(screen.getByRole("button", { name: /Versions/ }));
    await userEvent.click(screen.getByRole("button", { name: /2\.0k/ }));
    expect(await screen.findByText("the earlier text")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Save puts it back");
    expect(screen.getByRole("button", { name: /Save/ })).toBeEnabled();
  });

  it("says no version was kept for a document never saved from the page", async () => {
    await openEditor({ [`GET ${VERSIONS_URL}`]: [] });
    await userEvent.click(screen.getByRole("button", { name: /Versions/ }));
    expect(await screen.findByText(/no version was kept/)).toBeInTheDocument();
  });
});
