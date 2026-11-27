import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { installServer } from "../test/fakeServer";
import TranscriptView, { cleanPdfText } from "./TranscriptView";

const RUN_BASE = "/runs/knowledge/AcmeSync-demo";
const LINES = [
  { start_s: 0, end_s: 4, text: "Jonas opens the gateway." },
  { start_s: 65, end_s: 70, text: "The token goes to the provider." },
  { start_s: 3725, end_s: 3730, text: "Then the provider sends the token back." },
];

async function renderTranscript(playingS = 0) {
  installServer({ [`GET /api${RUN_BASE}/transcript`]: { lines: LINES } });
  const onPlayFrom = vi.fn();
  const view = render(<TranscriptView runBase={RUN_BASE} playingS={playingS} onPlayFrom={onPlayFrom} />);
  await screen.findByText("Jonas opens the gateway.");
  return { onPlayFrom, view };
}

const searchBox = () => screen.getByRole("combobox", { name: "search the transcript" });
const lineOf = (text: string) => screen.getByText(text).closest("li")!;

describe("TranscriptView", () => {
  it("lists every line with its time, and a time plays the recording from that line", async () => {
    const { onPlayFrom } = await renderTranscript();
    expect(screen.getByText("3 lines")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Download/ })).toHaveAttribute("href", `/api${RUN_BASE}/transcript/download`);
    fireEvent.click(within(lineOf("The token goes to the provider.")).getByRole("button", { name: "1:05" }));
    expect(onPlayFrom).toHaveBeenCalledWith(65);
    expect(within(lineOf("Then the provider sends the token back.")).getByRole("button", { name: "1:02:05" }))
      .toBeInTheDocument();
  });

  it("names the speaker where each turn starts, a colour per speaker, and speech said at once as unclear", async () => {
    const spoken = [
      { start_s: 0, end_s: 2, text: "Is the import done?", speaker: "Speaker A" },
      { start_s: 2, end_s: 4, text: "Yes, on Monday.", speaker: "Speaker B" },
      { start_s: 4, end_s: 6, text: "It took four hours.", speaker: "Speaker B" },
      { start_s: 6, end_s: 7, text: "Both at once.", speaker: null },
      { start_s: 7, end_s: 9, text: "Thanks.", speaker: "Speaker A" },
    ];
    installServer({ [`GET /api${RUN_BASE}/transcript`]: { lines: spoken } });
    render(<TranscriptView runBase={RUN_BASE} playingS={0} onPlayFrom={vi.fn()} />);
    await screen.findByText("Thanks.", { exact: false });
    const tags = [...document.querySelectorAll(".transcript-lines li")].map((row) => row.querySelector(".speaker-tag")?.textContent ?? "");
    expect(tags).toEqual(["Speaker A", "Speaker B", "", "unclear", "Speaker A"]);
    const [first, second] = document.querySelectorAll(".transcript-lines .speaker-tag");
    expect(first).toHaveClass("speaker-1");
    expect(second).toHaveClass("speaker-2");
  });

  it("names no speaker in a transcript without speakers", async () => {
    await renderTranscript();
    expect(document.querySelector(".speaker-tag")).toBeNull();
  });

  it("copies the transcript as the download writes it", async () => {
    await renderTranscript();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("[00:00] Jonas opens the gateway.\n"));
    fireEvent.click(screen.getByRole("button", { name: /Copy/ }));
    expect(await screen.findByText("Copied")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(`/api${RUN_BASE}/transcript/download`);
    expect(writeText).toHaveBeenCalledWith("[00:00] Jonas opens the gateway.\n");
    fetchMock.mockRestore();
  });

  it("marks the line being played", async () => {
    await renderTranscript(66);
    expect(lineOf("The token goes to the provider.")).toHaveAttribute("aria-current", "true");
    expect(lineOf("Jonas opens the gateway.")).not.toHaveAttribute("aria-current");
  });

  it("keeps the line being played in view as the recording plays, scrolling the list and never the page", async () => {
    const { view } = await renderTranscript(0);
    const list = document.querySelector<HTMLOListElement>(".transcript-lines")!;
    Object.defineProperty(list, "clientHeight", { configurable: true, value: 300 });
    Object.defineProperty(lineOf("Then the provider sends the token back."), "offsetTop", { configurable: true, value: 900 });
    view.rerender(<TranscriptView runBase={RUN_BASE} playingS={3726} onPlayFrom={vi.fn()} />);
    expect(list.scrollTop).toBe(900 - 100);
  });

  it("shows the lines that match while typing, near spellings too, with the matched words marked", async () => {
    await renderTranscript();
    fireEvent.change(searchBox(), { target: { value: "provder" } });
    const options = screen.getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual(["1:05The token goes to the provider.",
      "1:02:05Then the provider sends the token back."]);
    expect(within(options[0]).getByText("provider").tagName).toBe("MARK");
    expect(searchBox()).toHaveAttribute("aria-expanded", "true");
  });

  it("plays from a suggestion clicked, closes the list and marks the line found", async () => {
    const { onPlayFrom } = await renderTranscript();
    fireEvent.change(searchBox(), { target: { value: "sends" } });
    fireEvent.mouseDown(screen.getByRole("option"));
    fireEvent.click(screen.getByRole("option"));
    expect(onPlayFrom).toHaveBeenCalledWith(3725);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    expect(lineOf("Then the provider sends the token back.")).toHaveClass("found");
  });

  it("moves through the suggestions with the arrow keys, plays the chosen one on Enter, and closes on Escape", async () => {
    const { onPlayFrom } = await renderTranscript();
    fireEvent.change(searchBox(), { target: { value: "token" } });
    fireEvent.keyDown(searchBox(), { key: "ArrowDown" });
    fireEvent.keyDown(searchBox(), { key: "ArrowDown" });
    fireEvent.keyDown(searchBox(), { key: "ArrowUp" });
    fireEvent.keyDown(searchBox(), { key: "ArrowDown" });
    expect(screen.getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(searchBox(), { key: "Enter" });
    expect(onPlayFrom).toHaveBeenCalledWith(3725);
    fireEvent.change(searchBox(), { target: { value: "token" } });
    fireEvent.keyDown(searchBox(), { key: "Escape" });
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    fireEvent.keyDown(searchBox(), { key: "ArrowDown" });
    expect(screen.getByRole("listbox")).toBeInTheDocument();
  });

  it("plays the best suggestion on Enter when none was chosen", async () => {
    const { onPlayFrom } = await renderTranscript();
    fireEvent.change(searchBox(), { target: { value: "jonas" } });
    fireEvent.keyDown(searchBox(), { key: "Enter" });
    expect(onPlayFrom).toHaveBeenCalledWith(0);
  });

  it("completes the word being typed from the transcript's words, and Tab takes the completion", async () => {
    await renderTranscript();
    fireEvent.change(searchBox(), { target: { value: "the prov" } });
    expect(screen.getByTestId("completion")).toHaveTextContent("ider");
    fireEvent.keyDown(searchBox(), { key: "Tab" });
    expect(searchBox()).toHaveValue("the provider");
    expect(screen.queryByTestId("completion")).not.toBeInTheDocument();
  });

  it("says when no line matches", async () => {
    await renderTranscript();
    fireEvent.change(searchBox(), { target: { value: "printer" } });
    expect(screen.getByText("No line holds “printer”.")).toBeInTheDocument();
    fireEvent.keyDown(searchBox(), { key: "Enter" });
  });

  it("says when the run has no transcript yet, and when it could not be read", async () => {
    installServer({ [`GET /api${RUN_BASE}/transcript`]: { lines: [] } });
    const { unmount } = render(<TranscriptView runBase={RUN_BASE} playingS={0} onPlayFrom={vi.fn()} />);
    expect(await screen.findByText("No transcript yet: phase 1 writes it.")).toBeInTheDocument();
    unmount();
    installServer({});
    render(<TranscriptView runBase={RUN_BASE} playingS={0} onPlayFrom={vi.fn()} />);
    expect(await screen.findByText(/The transcript could not be read/)).toBeInTheDocument();
  });

  it("shows a PDF's text as Text: paragraphs with no time, no speaker and no markup", async () => {
    installServer({ [`GET /api${RUN_BASE}/transcript`]: { lines: [
      { start_s: 0, end_s: 12, text: "# **Gateway** is a <u>proxy <https://example.org/wiki/Proxy></u> for tokens.", speaker: null },
      { start_s: 12, end_s: 24, text: "It sends the token on.", speaker: null },
    ] } });
    render(<TranscriptView runBase={RUN_BASE} isPdf playingS={0} onPlayFrom={vi.fn()} />);
    const first = await screen.findByText("Gateway is a proxy for tokens.");
    expect(screen.getByText("Text")).toBeInTheDocument();
    expect(screen.getByText("2 paragraphs")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "search the text" })).toHaveAttribute("placeholder", "Search the text");
    expect(within(first.closest("li")!).queryByRole("button")).toBeNull();
    expect(screen.queryByText("unclear")).toBeNull();
  });

  it("takes a PDF paragraph's marks out and keeps its words", () => {
    expect(cleanPdfText("The term _photosynthesis_ means **oxygen <https://example.org/O>ic** ~~.~~"))
      .toBe("The term photosynthesis means oxygenic.");
  });
});
