import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ArtifactGroup } from "../api";
import { answerJson, answerText, installServer } from "../test/fakeServer";
import ArtifactsView, { describeFileOption, readFrameNumber, splitFrameDescriptions } from "./ArtifactsView";

const RUN_BASE = "/runs/knowledge/AcmeSync-demo";

function makeFrames(count: number): ArtifactGroup["items"] {
  return Array.from({ length: count }, (_, index) => ({
    path: `frames/frame-${String(index + 1).padStart(3, "0")}.jpg`,
    name: `frame-${String(index + 1).padStart(3, "0")}.jpg`,
    size_bytes: 40_000,
    time_s: index === 1 ? null : 7 + index * 60,
  }));
}

const GROUPS: ArtifactGroup[] = [
  { key: "document", title: "Document", kind: "markdown", items: [
    { path: "AcmeSync-demo/AcmeSync-demo.md", name: "AcmeSync-demo.md", size_bytes: 900 },
    { path: "04_document.md", name: "04_document.md", size_bytes: 800 }] },
  { key: "frames", title: "Frames", kind: "images", items: makeFrames(3) },
  { key: "audio", title: "Audio and transcript", kind: "audio", items: [{ path: "audio.wav", name: "audio.wav", size_bytes: 1000 }] },
];

function serveArtifacts(groups: object = GROUPS) {
  return installServer({
    [`GET /api${RUN_BASE}/artifacts`]: groups,
    [`GET /api${RUN_BASE}/files/AcmeSync-demo/AcmeSync-demo.md`]: answerText("# Published Gateway notes"),
    [`GET /api${RUN_BASE}/files/04_document.md`]: answerText("# Draft of Jonas"),
    [`GET /api${RUN_BASE}/transcript`]: { lines: [{ start_s: 42, end_s: 45, text: "Jonas opens the gateway." }] },
  });
}

describe("ArtifactsView", () => {
  it("a clicked frame shows its own description and what was said while it was on screen", async () => {
    installServer({
      [`GET /api${RUN_BASE}/artifacts`]: [{ key: "frames", title: "Frames", kind: "images", items: makeFrames(2) },
        { key: "descriptions", title: "Frame descriptions", kind: "markdown",
          items: [{ path: "03_frame-descriptions.md", name: "03_frame-descriptions.md", size_bytes: 90 }] }],
      [`GET /api${RUN_BASE}/files/03_frame-descriptions.md`]: answerText(
        "# Frame descriptions\n\n## Frame 1 · 00:07\n\nSaid while it is on screen: Jonas opens the gateway\n\n- the gateway signs tokens\n"
        + "\n## Frame 2 · 01:07\n\nSaid while it is on screen: (nothing is said)\n\n- AcmeSync board\n"),
    });
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={false} onPlayFrom={vi.fn()} />);
    await userEvent.click((await screen.findAllByRole("button", { name: /frame-00/ }))[1]);
    const shown = await screen.findByLabelText("frame description");
    expect(shown).toHaveTextContent("- AcmeSync board");
    expect(shown).not.toHaveTextContent("gateway");
    expect(readFrameNumber("frame-045.jpg")).toBe(45);
    expect(splitFrameDescriptions("## Frame 3 · 00:30\n\nx\n").get(3)).toBe("x");
  });

  it("an enlarged frame opens its own notes file", async () => {
    installServer({
      [`GET /api${RUN_BASE}/artifacts`]: [{ key: "frames", title: "Frames", kind: "images", items: makeFrames(2) },
        { key: "processed", title: "Frame notes", kind: "markdown", items: [
          { path: "03_processed-frames/frame-001.md", name: "frame-001.md", size_bytes: 90 },
          { path: "03_processed-frames/frame-002.md", name: "frame-002.md", size_bytes: 90 }] }],
      [`GET /api${RUN_BASE}/files/03_processed-frames/frame-002.md`]: answerText("# Frame 2 · AcmeSync board"),
    });
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={false} onPlayFrom={vi.fn()} />);
    await userEvent.click((await screen.findAllByRole("button", { name: /frame-00/ }))[1]);
    await userEvent.click(screen.getByRole("button", { name: "Read its notes" }));
    expect(await screen.findByText("Frame 2 · AcmeSync board")).toBeInTheDocument();
  });

  it("sets aside the cuts the document never placed, from the Cuts group", async () => {
    const calls = installServer({
      [`GET /api${RUN_BASE}/artifacts`]: [{ key: "cuts", title: "Cuts", kind: "images",
        items: [{ path: "04_writing/images/cuts/cut-001.png", name: "cut-001.png", size_bytes: 9 }] }],
      [`POST /api${RUN_BASE}/cuts/tidy`]: { exit_code: 0, output: "1 cut moved into .trash/cuts" },
    });
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={false} onPlayFrom={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Set aside unused cuts" }));
    expect(calls.some((call) => call.method === "POST" && call.url === `/api${RUN_BASE}/cuts/tidy`)).toBe(true);
    expect(await screen.findByText("1 cut moved into .trash/cuts")).toBeInTheDocument();
  });

  it("leaves out Audio and transcript for an audio run the Recording tab plays, and keeps it otherwise", async () => {
    serveArtifacts();
    const { unmount } = render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording isAudio onPlayFrom={vi.fn()} />);
    expect(await screen.findByRole("tab", { name: /Document/ })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Audio and transcript/ })).not.toBeInTheDocument();
    unmount();
    serveArtifacts();
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={false} isAudio onPlayFrom={vi.fn()} />);
    expect(await screen.findByRole("tab", { name: /Audio and transcript/ })).toBeInTheDocument();
  });

  it("Refine ticks the frames it would drop; the person changes the ticks, confirms, and the page sends both", async () => {
    const proposal = [1, 2, 3].map((number) => ({ slug: `frame-00${number}`, path: `frames/frame-00${number}.jpg`,
      timestamp: number * 60, dropScore: number === 1 ? 0.95 : number === 2 ? 0.9 : 0.1,
      decision: number === 3 ? "keep" : "drop" }));
    const calls = installServer({
      [`GET /api${RUN_BASE}/artifacts`]: [{ key: "frames", title: "Frames", kind: "images", items: makeFrames(3) }],
      [`POST /api${RUN_BASE}/frames/refine`]: { exit_code: 0, output: "refine-frames: 2 of 3", records: proposal },
      [`POST /api${RUN_BASE}/frames/drop`]: { exit_code: 0, output: "drop-frames: dropped" },
    });
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={true} onPlayFrom={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: /Refine/ }));
    expect(await screen.findByText(/Refine proposes 2 of 3/)).toBeInTheDocument();
    expect(screen.getByLabelText("select frame-001.jpg")).toBeChecked();
    expect(screen.getByLabelText("select frame-003.jpg")).not.toBeChecked();
    expect(screen.getByText("proposed · 0.95")).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("select frame-002.jpg"));
    await userEvent.click(screen.getByLabelText("select frame-003.jpg"));
    await userEvent.click(screen.getByRole("button", { name: /Accept proposal \(2\)/ }));
    expect(screen.getByText(/1 FP \(proposed, you keep them\) and 1 FN/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Accept" }));
    const sent = calls.find((call) => call.url.endsWith("/frames/drop"))!.body;
    expect(sent).toEqual({ frames: ["frame-001", "frame-003"], proposed: ["frame-001", "frame-002"],
      drop_scores: { "frame-001": 0.95, "frame-002": 0.9, "frame-003": 0.1 } });
    expect(await screen.findByText("drop-frames: dropped")).toBeInTheDocument();
  });

  it("Refine's icon turns while the classifier works, and the button keeps its label", async () => {
    let finish: (value: object) => void = () => undefined;
    installServer({
      [`GET /api${RUN_BASE}/artifacts`]: [{ key: "frames", title: "Frames", kind: "images", items: makeFrames(1) }],
      [`POST /api${RUN_BASE}/frames/refine`]: () => new Promise((resolve) => { finish = resolve; }),
    });
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={true} onPlayFrom={vi.fn()} />);
    const button = await screen.findByRole("button", { name: /Refine/ });
    expect(button.querySelector(".spinning")).toBeNull();
    await userEvent.click(button);
    expect(button.querySelector(".spinning")).not.toBeNull();
    expect(button).toHaveTextContent(/^\s*Refine$/);
    expect(button).toBeDisabled();
    await act(async () => finish({ exit_code: 0, output: "", records: [] }));
    expect(button.querySelector(".spinning")).toBeNull();
  });

  it("a proposal of nothing can be accepted as it stands, which saves no learning data; with no proposal the button drops", async () => {
    const calls = installServer({
      [`GET /api${RUN_BASE}/artifacts`]: [{ key: "frames", title: "Frames", kind: "images", items: makeFrames(2) }],
      [`POST /api${RUN_BASE}/frames/refine`]: { exit_code: 0, output: "", records: [
        { slug: "frame-001", path: "frames/frame-001.jpg", timestamp: 7, dropScore: 0.1, decision: "keep" }] },
      [`POST /api${RUN_BASE}/frames/drop`]: { exit_code: 0,
        output: "drop-frames: dropped 0 frame(s)\nlearning data: the proposal was accepted as it stands; nothing to correct" },
    });
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={true} onPlayFrom={vi.fn()} />);
    expect(await screen.findByRole("button", { name: /Drop selected/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /Refine/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Accept proposal (0)" }));
    expect(screen.getByText(/as it stands: no learning data is saved/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Accept" }));
    expect(calls.find((call) => call.url.endsWith("/frames/drop"))!.body).toEqual(
      { frames: [], proposed: [], drop_scores: { "frame-001": 0.1 } });
    expect(await screen.findByText(/nothing to correct/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Drop selected/ })).toBeInTheDocument();
  });

  it("Reject proposal unticks every frame and drops nothing; the buttons are off while the run writes", async () => {
    const calls = installServer({
      [`GET /api${RUN_BASE}/artifacts`]: [{ key: "frames", title: "Frames", kind: "images", items: makeFrames(2) },
        { key: "dropped", title: "Dropped frames", kind: "images", items: makeFrames(1) }],
      [`POST /api${RUN_BASE}/frames/refine`]: { exit_code: 0, output: "", records: [
        { slug: "frame-001", path: "frames/frame-001.jpg", timestamp: 7, dropScore: 0.9, decision: "drop" }] },
    });
    const { unmount } = render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={true} onPlayFrom={vi.fn()} />);
    await userEvent.click(await screen.findByRole("button", { name: /Refine/ }));
    await userEvent.click(await screen.findByRole("button", { name: "Reject proposal" }));
    expect(screen.getByLabelText("select frame-001.jpg")).not.toBeChecked();
    expect(screen.queryByText(/proposed ·/)).not.toBeInTheDocument();
    expect(calls.some((call) => call.url.endsWith("/frames/drop"))).toBe(false);
    await userEvent.click(screen.getByRole("tab", { name: /Dropped frames/ }));
    expect(screen.queryByRole("button", { name: /Refine/ })).not.toBeInTheDocument();
    unmount();

    render(<ArtifactsView runBase={RUN_BASE} isLive={true} hasRecording={true} onPlayFrom={vi.fn()} />);
    await userEvent.click(await screen.findByRole("tab", { name: /^Frames/ }));
    expect(screen.getByRole("button", { name: /Refine/ })).toBeDisabled();
  });

  it("shows the dropped frames as their own group, each with its drop score, and a kept frame's score too", async () => {
    serveArtifacts([
      { key: "frames", title: "Frames", kind: "images", items: [{ ...makeFrames(1)[0], drop_score: 0.12 }] },
      { key: "dropped", title: "Dropped frames", kind: "images", items: [
        { path: "frames/dropped/frame-002.jpg", name: "frame-002.jpg", size_bytes: 1, time_s: 67, drop_score: 0.97 }] },
    ]);
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={true} onPlayFrom={vi.fn()} />);
    expect(await screen.findByText(/drop score 0\.12/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /Dropped frames/ }));
    const tile = await screen.findByRole("button", { name: /frame-002/ });
    expect(within(tile).getByText(/drop score 0\.97/)).toBeInTheDocument();
    expect(screen.queryByText(/drop score 0\.12/)).not.toBeInTheDocument();
  });

  it("names the layer that dropped each frame, and for the redundancy filter the frame that holds it", async () => {
    serveArtifacts([
      { key: "dropped", title: "Dropped frames", kind: "images", items: [
        { path: "frames/dropped/frame-002.jpg", name: "frame-002.jpg", size_bytes: 1, time_s: 67, drop_score: 0.97,
          dropped_by: "filter" },
        { path: "frames/dropped/frame-004.jpg", name: "frame-004.jpg", size_bytes: 1, time_s: 90,
          dropped_by: "redundancy", covered_by: ["frame-005"], drop_reason: "the same ticket again" }] },
    ]);
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={true} onPlayFrom={vi.fn()} />);
    await userEvent.click(await screen.findByRole("tab", { name: /Dropped frames/ }));
    const classified = await screen.findByRole("button", { name: /frame-002/ });
    expect(within(classified).getByText(/dropped by knowledge filter/)).toBeInTheDocument();
    const redundant = screen.getByRole("button", { name: /frame-004/ });
    const label = within(redundant).getByText(/dropped by redundancy filter: held by frame-005/);
    expect(label).toHaveAttribute("title", "the same ticket again");
  });

  it("names the kind of empty frame the decision model dropped a frame as", async () => {
    serveArtifacts([
      { key: "dropped", title: "Dropped frames", kind: "images", items: [
        { path: "frames/dropped/frame-003.jpg", name: "frame-003.jpg", size_bytes: 1, time_s: 12, drop_score: 0.92,
          dropped_by: "filter", drop_class: "desktop" }] },
    ]);
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={true} onPlayFrom={vi.fn()} />);
    await userEvent.click(await screen.findByRole("tab", { name: /Dropped frames/ }));
    const tile = await screen.findByRole("button", { name: /frame-003/ });
    expect(within(tile).getByText(/dropped by knowledge filter \(desktop\)/)).toBeInTheDocument();
  });

  it("shows each frame's tag from the frame classifier, in the Frames and the Dropped frames tabs, but no \"other\" on a kept frame", async () => {
    serveArtifacts([
      { key: "frames", title: "Frames", kind: "images", items: [
        { ...makeFrames(1)[0], tag: "text" }, { ...makeFrames(2)[1], tag: "other" }] },
      { key: "dropped", title: "Dropped frames", kind: "images", items: [
        { path: "frames/dropped/frame-009.jpg", name: "frame-009.jpg", size_bytes: 1, time_s: 9, tag: "people" }] },
    ]);
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={true} onPlayFrom={vi.fn()} />);
    expect(await screen.findByText("text")).toHaveClass("tag-badge", "tag-text");
    // a kept frame tagged other is what every kept frame is unless marked text: the owner wants no label for it
    expect(screen.queryByText("other")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /Dropped frames/ }));
    expect(await screen.findByText("people")).toHaveClass("tag-people");
  });

  it("says while it loads, when the list fails, and when the run produced nothing", async () => {
    installServer({ [`GET /api${RUN_BASE}/artifacts`]: answerJson({ detail: "unknown run folder" }, 404) });
    const { unmount } = render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={true} onPlayFrom={vi.fn()} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(await screen.findByText("404: unknown run folder")).toHaveClass("error");
    unmount();
    serveArtifacts([]);
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={true} onPlayFrom={vi.fn()} />);
    expect(await screen.findByText("This run has produced nothing to look at yet.")).toBeInTheDocument();
  });

  it("opens on the first group, the published document first, and switches files", async () => {
    serveArtifacts();
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={true} onPlayFrom={vi.fn()} />);
    expect(await screen.findByRole("heading", { name: "Published Gateway notes" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Document 2" })).toHaveAttribute("aria-selected", "true");
    await userEvent.selectOptions(screen.getByRole("combobox"), "04_document.md");
    expect(await screen.findByRole("heading", { name: "Draft of Jonas" })).toBeInTheDocument();
    expect(screen.queryByText(/updating while the run writes/)).not.toBeInTheDocument();
  });

  it("names each version of the document by what it is before its path, any other file by its path", () => {
    // The owner: three paths in the list told nothing of which was the original, a part's draft or the document
    expect(describeFileOption({ path: "07_reshaping/parts/part-001.md", name: "part-001.md", size_bytes: 1,
      label: "knowledge draft after part 1 of 5" })).toBe("Knowledge draft after part 1 of 5 · 07_reshaping/parts/part-001.md");
    expect(describeFileOption({ path: "05_review/review.md", name: "review.md", size_bytes: 1 })).toBe("05_review/review.md");
  });

  it("moves to the next and the previous file of a group with the buttons beside its list", async () => {
    serveArtifacts();
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={true} onPlayFrom={vi.fn()} />);
    const list = await screen.findByRole("combobox");
    await userEvent.selectOptions(list, (list as HTMLSelectElement).options[0].value);
    expect(await screen.findByRole("heading", { name: "Published Gateway notes" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "previous file" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "next file" }));
    expect(await screen.findByRole("heading", { name: "Draft of Jonas" })).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toHaveValue("04_document.md");
    expect(screen.getByRole("button", { name: "next file" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "previous file" }));
    expect(screen.getByRole("combobox")).toHaveValue("AcmeSync-demo/AcmeSync-demo.md");
    expect(screen.getByRole("button", { name: "previous file" })).toBeDisabled();
  });

  it("shows each frame with its time, and the large view moves by buttons and arrow keys", async () => {
    serveArtifacts();
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={true} onPlayFrom={vi.fn()} />);
    await userEvent.click(await screen.findByRole("tab", { name: "Frames 3" }));
    const thumbs = screen.getAllByRole("button", { name: /frame-00/ });
    expect(thumbs.map((thumb) => thumb.querySelector("span")?.textContent)).toEqual(["frame-001 · 0:07", "frame-002", "frame-003 · 2:07"]);
    expect(thumbs[0].querySelector("img")).toHaveAttribute("src", `/api${RUN_BASE}/files/frames/frame-001.jpg`);

    await userEvent.click(thumbs[0]);
    expect(screen.getByRole("heading", { name: "frame-001.jpg · at 0:07 · 1 of 3" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Previous/ })).toBeDisabled();
    expect(screen.getByRole("link", { name: /Download/ })).toHaveAttribute("href", `/api${RUN_BASE}/download/frames/frame-001.jpg`);
    await userEvent.click(screen.getByRole("button", { name: /Next/ }));
    expect(screen.getByRole("heading", { name: "frame-002.jpg · 2 of 3" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Play from/ })).not.toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowRight" });
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByRole("heading", { name: "frame-003.jpg · at 2:07 · 3 of 3" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Next/ })).toBeDisabled();
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    fireEvent.keyDown(window, { key: "x" });
    await userEvent.click(screen.getByRole("button", { name: /Previous/ }));
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(screen.getByRole("heading", { name: /frame-001.jpg/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "close" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("Play from mm:ss closes the frame and hands its second to the detail view, which opens the Recording", async () => {
    const onPlayFrom = vi.fn();
    serveArtifacts();
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={true} onPlayFrom={onPlayFrom} />);
    await userEvent.click(await screen.findByRole("tab", { name: "Frames 3" }));
    await userEvent.click(screen.getAllByRole("button", { name: /frame-00/ })[2]);
    await userEvent.click(screen.getByRole("button", { name: "Play from 2:07" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onPlayFrom).toHaveBeenCalledWith(127);
    expect(screen.queryByRole("tab", { name: /Recording/ })).not.toBeInTheDocument();
  });

  it("offers no Play from when the recording is gone, and pages the frames 60 at a time", async () => {
    serveArtifacts([{ key: "frames", title: "Frames", kind: "images", items: makeFrames(130) }]);
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={false} onPlayFrom={vi.fn()} />);
    expect(await screen.findAllByRole("button", { name: /^frame-/ })).toHaveLength(60);
    await userEvent.click(screen.getByRole("button", { name: "Show more (70 left)" }));
    await userEvent.click(screen.getByRole("button", { name: "Show more (10 left)" }));
    expect(screen.getAllByRole("button", { name: /^frame-/ })).toHaveLength(130);
    expect(screen.queryByRole("button", { name: /Show more/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: /^frame-/ })[0]);
    expect(screen.queryByRole("button", { name: /Play from/ })).not.toBeInTheDocument();
  });

  it("selects images, downloads them as one zip, and hides and shows them again for good", async () => {
    const frame = (number: number, isHidden: boolean) => ({ path: `frames/frame-00${number}.jpg`,
      name: `frame-00${number}.jpg`, size_bytes: 1, time_s: number * 60, is_hidden: isHidden });
    let hidden = new Set(["frames/frame-003.jpg"]);
    let isZipBroken = false;
    const calls = installServer({
      [`GET /api${RUN_BASE}/artifacts`]: () => [{ key: "frames", title: "Frames", kind: "images",
        items: [1, 2, 3].map((number) => frame(number, hidden.has(`frames/frame-00${number}.jpg`))) }],
      [`POST /api${RUN_BASE}/zip`]: () => (isZipBroken ? answerJson({ detail: "a frame is missing" }, 500) : new Response("zip")),
      [`POST /api${RUN_BASE}/hidden`]: (body) => {
        const { paths, is_hidden } = body as { paths: string[]; is_hidden: boolean };
        hidden = new Set(is_hidden ? [...hidden, ...paths] : [...hidden].filter((path) => !paths.includes(path)));
        return { hidden: [...hidden] };
      },
    });
    URL.createObjectURL = vi.fn(() => "blob:zip");
    URL.revokeObjectURL = vi.fn();
    const save = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const zipBodies = () => calls.filter((call) => call.url.endsWith("/zip")).map((call) => call.body);
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={false} onPlayFrom={vi.fn()} />);

    expect(await screen.findByText("2 shown")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /frame-003/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Download selected/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "select frame-001.jpg" }));
    expect(screen.getByText("1 selected")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Download selected/ }));
    await userEvent.click(screen.getByRole("button", { name: /Download all \(2\)/ }));
    expect(zipBodies()).toEqual([{ paths: ["frames/frame-001.jpg"], label: "frames" },
      { paths: ["frames/frame-001.jpg", "frames/frame-002.jpg"], label: "frames" }]);
    // the zip is saved once its response is read, a tick after the request: checked at once, it was 0 or 2 by timing
    await waitFor(() => expect(save).toHaveBeenCalledTimes(2));

    isZipBroken = true;
    await userEvent.click(screen.getByRole("button", { name: /Download all/ }));
    expect(await screen.findByText("500: a frame is missing")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Hide selected" }));
    expect(await screen.findByText("hid 1 image(s)")).toBeInTheDocument();
    expect(await screen.findByText("1 shown")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: /Show hidden \(2\)/ }));
    expect(screen.getByText("3 shown")).toBeInTheDocument();
    expect(screen.getAllByText("hidden")).toHaveLength(2);
    await userEvent.click(screen.getByRole("checkbox", { name: "select every image shown" }));
    expect(screen.getByText("3 selected")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show selected again" }));
    expect(calls.filter((call) => call.url.endsWith("/hidden")).at(-1)?.body)
      .toEqual({ paths: ["frames/frame-001.jpg", "frames/frame-003.jpg"], is_hidden: false });
    expect(await screen.findByText("showed again 2 image(s)")).toBeInTheDocument();
    expect(screen.queryByText(/Show hidden/)).not.toBeInTheDocument();
  });

  it("plays the audio with a download link, and lists the transcript under it, a line's time playing the audio there", async () => {
    serveArtifacts();
    const onPlayFrom = vi.fn();
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={true} onPlayFrom={onPlayFrom} />);
    await userEvent.click(await screen.findByRole("tab", { name: "Audio and transcript 1" }));
    const card = document.querySelector<HTMLElement>(".audio")!;
    const audio = card.querySelector("audio")!;
    expect(audio).toHaveAttribute("src", `/api${RUN_BASE}/files/audio.wav`);
    expect(within(card).getByRole("link", { name: /Download/ })).toHaveAttribute("href", `/api${RUN_BASE}/download/audio.wav`);
    await screen.findByText("Jonas opens the gateway.");
    const play = vi.spyOn(HTMLMediaElement.prototype, "play");
    await userEvent.click(screen.getByRole("button", { name: "0:42" }));
    expect(audio.currentTime).toBe(42);
    expect(play).toHaveBeenCalled();
    expect(onPlayFrom).not.toHaveBeenCalled();
    fireEvent.timeUpdate(audio);
  });

  it("reads the list again every 5 seconds while the run is live, and stops when it ends", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const calls = serveArtifacts();
    const countListReads = () => calls.filter((call) => call.url.endsWith("/artifacts")).length;
    const { rerender } = render(<ArtifactsView runBase={RUN_BASE} isLive hasRecording={true} onPlayFrom={vi.fn()} />);
    expect(await screen.findByText("updating while the run writes")).toBeInTheDocument();
    expect(countListReads()).toBe(1);
    await act(async () => vi.advanceTimersByTime(5000));
    expect(countListReads()).toBe(2);
    rerender(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording={true} onPlayFrom={vi.fn()} />);
    await act(async () => vi.advanceTimersByTime(15000));
    expect(countListReads()).toBe(2);
  });
});

describe("ArtifactsView of a PDF", () => {
  it("shows a PDF's figures by their page, with no tool that drops a video's frames", async () => {
    installServer({ [`GET /api${RUN_BASE}/artifacts`]: [{ key: "frames", title: "Figures", kind: "images", items: [
      { path: "frames/frame-000.jpg", name: "frame-000.jpg", size_bytes: 10, time_s: 59.99, page: 3 }] }] });
    render(<ArtifactsView runBase={RUN_BASE} isLive={false} hasRecording onPlayFrom={vi.fn()} />);
    expect(await screen.findByText(/page 3/)).toBeInTheDocument();
    expect(screen.queryByText(/0:59/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Refine/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Drop selected/ })).not.toBeInTheDocument();
  });
});
