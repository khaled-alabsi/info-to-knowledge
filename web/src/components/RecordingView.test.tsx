import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { installServer } from "../test/fakeServer";
import styles from "../styles.css?raw";
import RecordingView from "./RecordingView";

const RUN_BASE = "/runs/knowledge/AcmeSync-demo";
const ONE_PART = [{ name: "AcmeSync-demo.mp4", size_bytes: 9, start_s: 0, duration_s: 600 }];
const THREE_PARTS = [{ name: "part 1.mp4", size_bytes: 9, start_s: 0, duration_s: 100 },
  { name: "part 2.mp4", size_bytes: 9, start_s: 100, duration_s: 50 },
  { name: "part 3.mp4", size_bytes: 9, start_s: 150, duration_s: 80 }];

describe("RecordingView", () => {
  it("plays the run's recording from its own route, with no line saying what one file is", () => {
    render(<RecordingView runBase={RUN_BASE} name="AcmeSync-demo.mp4" parts={ONE_PART} seekTo={null} onSeeked={vi.fn()} />);
    expect(document.querySelector("video")).toHaveAttribute("src", `/api${RUN_BASE}/recording`);
    expect(screen.getByText("AcmeSync-demo.mp4")).toHaveAttribute("title", "AcmeSync-demo.mp4");
    expect(screen.queryByText(/the recording this run was made from/)).not.toBeInTheDocument();
    const download = screen.getByRole("link", { name: /Download/ });
    expect(download).toHaveAttribute("href", `/api${RUN_BASE}/recording`);
    expect(download).toHaveAttribute("download", "AcmeSync-demo.mp4");
  });

  it("shows a PDF alone, with no text panel stacked under it defect", () => {
    // Defect: a PDF's text stood in a panel stacked under the PDF. Fix: the PDF's tab holds the PDF alone; its text is
    // an artifact in the Artifacts tab
    const { container } = render(<RecordingView runBase={RUN_BASE} name="AcmeSync-guide.pdf" seekTo={null} onSeeked={vi.fn()}
      parts={[{ name: "AcmeSync-guide.pdf", size_bytes: 9, start_s: 0, duration_s: 0 }]}
      batches={[{ start_s: 0, end_s: 60, title: "Gateway setup", summary: "" }]} />);
    expect(container.querySelector(".recording-side")).toBeNull();
    expect(screen.queryByRole("tab", { name: "Text" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Timestamps" })).not.toBeInTheDocument();
    expect(screen.getByText("AcmeSync-guide.pdf")).toBeInTheDocument();
  });

  it("stands the transcript panel under an audio player, beside a video", () => {
    const { container, rerender } = render(<RecordingView runBase={RUN_BASE} name="AcmeSync-call.wav" parts={ONE_PART}
      isAudio seekTo={null} onSeeked={vi.fn()} />);
    expect(container.querySelector(".recording-layout")).toHaveClass("audio");
    rerender(<RecordingView runBase={RUN_BASE} name="AcmeSync-demo.mp4" parts={ONE_PART} seekTo={null} onSeeked={vi.fn()} />);
    expect(container.querySelector(".recording-layout")).not.toHaveClass("audio");
  });

  it("says the recording's size on its Download button, in GB from a gigabyte on and in MB below it", () => {
    const { rerender } = render(<RecordingView runBase={RUN_BASE} name="AcmeSync-demo.mp4" seekTo={null} onSeeked={vi.fn()}
      parts={[{ name: "AcmeSync-demo.mp4", size_bytes: 8.1e9, start_s: 0, duration_s: 60 }]} />);
    expect(screen.getByRole("link", { name: /Download/ })).toHaveTextContent("Download · 8.1 GB");
    rerender(<RecordingView runBase={RUN_BASE} name="AcmeSync-demo.mp4" seekTo={null} onSeeked={vi.fn()}
      parts={[{ name: "AcmeSync-demo.mp4", size_bytes: 294e6, start_s: 0, duration_s: 60 }]} />);
    expect(screen.getByRole("link", { name: /Download/ })).toHaveTextContent("Download · 294 MB");
  });

  it("starts at the given second once the recording's length is known, then clears the request", () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play");
    const onSeeked = vi.fn();
    render(<RecordingView runBase={RUN_BASE} name="AcmeSync-demo.mp4" parts={ONE_PART} seekTo={127} onSeeked={onSeeked} />);
    const player = document.querySelector<HTMLVideoElement>("video")!;
    expect(play).not.toHaveBeenCalled();
    fireEvent(player, new Event("loadedmetadata"));
    expect(player.currentTime).toBe(127);
    expect(play).toHaveBeenCalledTimes(1);
    expect(onSeeked).toHaveBeenCalledTimes(1);
  });

  it("seeks at once when the length is already known, and a refused autoplay breaks nothing", () => {
    vi.spyOn(HTMLMediaElement.prototype, "readyState", "get").mockReturnValue(1);
    const play = vi.spyOn(HTMLMediaElement.prototype, "play").mockRejectedValue(new Error("autoplay refused"));
    const onSeeked = vi.fn();
    render(<RecordingView runBase={RUN_BASE} name="AcmeSync-demo.mp4" parts={ONE_PART} seekTo={7} onSeeked={onSeeked} />);
    expect(document.querySelector<HTMLVideoElement>("video")!.currentTime).toBe(7);
    expect(play).toHaveBeenCalledTimes(1);
    expect(onSeeked).toHaveBeenCalledTimes(1);
  });

  it("opens the part a second of the meeting falls in, at that second inside the part, and moves on at a part's end", () => {
    const play = vi.spyOn(HTMLMediaElement.prototype, "play");
    const onSeeked = vi.fn();
    const { rerender } = render(<RecordingView runBase={RUN_BASE} name="Weekly" parts={THREE_PARTS} seekTo={null} onSeeked={onSeeked} />);
    expect(screen.getByRole("tab", { name: "Part 1" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByText("Weekly · part 1.mp4")).toBeInTheDocument();
    rerender(<RecordingView runBase={RUN_BASE} name="Weekly" parts={THREE_PARTS} seekTo={130} onSeeked={onSeeked} />);
    expect(screen.getByRole("tab", { name: "Part 2" })).toHaveAttribute("aria-selected", "true");
    const player = document.querySelector<HTMLVideoElement>("video")!;
    expect(player).toHaveAttribute("src", `/api${RUN_BASE}/recording?part=1`);
    fireEvent(player, new Event("loadedmetadata"));
    expect(player.currentTime).toBe(30);
    expect(play).toHaveBeenCalled();
    expect(screen.getByRole("link", { name: /Download/ })).toHaveAttribute("href", `/api${RUN_BASE}/recording?part=1`);
    fireEvent(player, new Event("ended"));
    expect(screen.getByRole("tab", { name: "Part 3" })).toHaveAttribute("aria-selected", "true");
    fireEvent(document.querySelector("video")!, new Event("ended"));
    expect(screen.getByRole("tab", { name: "Part 3" })).toHaveAttribute("aria-selected", "true");
    act(() => screen.getByRole("tab", { name: "Part 1" }).click());
    expect(document.querySelector("video")).toHaveAttribute("src", `/api${RUN_BASE}/recording`);
  });

  it("seeks inside the part already on screen without reloading it", () => {
    vi.spyOn(HTMLMediaElement.prototype, "readyState", "get").mockReturnValue(1);
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
    const { rerender } = render(<RecordingView runBase={RUN_BASE} name="Weekly" parts={THREE_PARTS} seekTo={null} onSeeked={vi.fn()} />);
    rerender(<RecordingView runBase={RUN_BASE} name="Weekly" parts={THREE_PARTS} seekTo={42} onSeeked={vi.fn()} />);
    expect(document.querySelector<HTMLVideoElement>("video")!.currentTime).toBe(42);
  });

  it("lists the batches by title beside the player, plays from a batch's first second, and marks the batch playing", async () => {
    const onPlayFrom = vi.fn();
    const batches = [{ title: "Gateway login flow", start_s: 0, end_s: 120, summary: "" },
      { title: "", start_s: 120, end_s: 250, summary: "" }];
    render(<RecordingView runBase={RUN_BASE} name="AcmeSync-demo.mp4" parts={ONE_PART} batches={batches}
      onPlayFrom={onPlayFrom} seekTo={null} onSeeked={vi.fn()} />);
    const list = screen.getByRole("navigation", { name: "timestamps" });
    expect(list).toHaveTextContent("0:00Gateway login flow");
    expect(list).toHaveTextContent("2:002:00 to 4:10");
    act(() => screen.getByRole("button", { name: /2:00 to 4:10/ }).click());
    expect(onPlayFrom).toHaveBeenCalledWith(120);
    const player = document.querySelector<HTMLVideoElement>("video")!;
    expect(screen.getByRole("button", { name: /Gateway login flow/ })).toHaveAttribute("aria-current", "true");
    player.currentTime = 130;
    fireEvent(player, new Event("timeupdate"));
    expect(screen.getByRole("button", { name: /2:00 to 4:10/ })).toHaveAttribute("aria-current", "true");
    expect(screen.getByRole("button", { name: /Gateway login flow/ })).not.toHaveAttribute("aria-current");
  });

  it("sets one panel beside the player, as tall as it, with the timestamps and the transcript as its two tabs", async () => {
    installServer({ [`GET /api${RUN_BASE}/transcript`]: { lines: [{ start_s: 42, end_s: 45, text: "Jonas opens the gateway." }] } });
    const batches = [{ title: "Gateway login flow", start_s: 0, end_s: 120, summary: "" }];
    render(<RecordingView runBase={RUN_BASE} name="AcmeSync-demo.mp4" parts={ONE_PART} batches={batches}
      onPlayFrom={vi.fn()} seekTo={null} onSeeked={vi.fn()} />);
    // The owner: the video on the whole left, the timestamps and the transcript in one panel beside it, as tall as it
    const layout = document.querySelector(".recording-layout")!;
    expect([...layout.children].map((child) => child.className.split(" ").at(-1))).toEqual(["recording-player", "recording-side"]);
    expect(layout.querySelector(".recording-player video")).not.toBeNull();
    const rule = styles.match(/^\.recording-side \{([^}]*)\}/m)![1];
    expect(rule).toContain("contain: size");
    expect(screen.getByRole("tab", { name: "Timestamps" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("navigation", { name: "timestamps" })).toBeInTheDocument();
    expect(screen.queryByText("Jonas opens the gateway.")).toBeNull();
    act(() => screen.getByRole("tab", { name: "Transcript" }).click());
    expect(await screen.findByText("Jonas opens the gateway.")).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "timestamps" })).toBeNull();
  });

  it("shows the transcript alone in the panel before phase 3 has written a batch", async () => {
    installServer({ [`GET /api${RUN_BASE}/transcript`]: { lines: [{ start_s: 42, end_s: 45, text: "Jonas opens the gateway." }] } });
    render(<RecordingView runBase={RUN_BASE} name="AcmeSync-demo.mp4" parts={ONE_PART} seekTo={null} onSeeked={vi.fn()} />);
    expect(screen.queryByRole("tab", { name: "Timestamps" })).toBeNull();
    expect(screen.getByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("Jonas opens the gateway.")).toBeInTheDocument();
  });

  it("shows no batch list before phase 3 has written a batch", () => {
    render(<RecordingView runBase={RUN_BASE} name="AcmeSync-demo.mp4" parts={ONE_PART} seekTo={null} onSeeked={vi.fn()} />);
    expect(screen.queryByRole("navigation", { name: "timestamps" })).toBeNull();
  });

  it("opens a timestamp to show its summary and closes it again, and offers no opening without a summary", () => {
    const batches = [{ title: "Gateway login flow", start_s: 0, end_s: 120, summary: "Jonas logs in through the gateway." },
      { title: "AcmeSync setup", start_s: 120, end_s: 250, summary: "" }];
    render(<RecordingView runBase={RUN_BASE} name="AcmeSync-demo.mp4" parts={ONE_PART} batches={batches}
      seekTo={null} onSeeked={vi.fn()} />);
    expect(screen.getByRole("tab", { name: "Timestamps" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByText("Jonas logs in through the gateway.")).toBeNull();
    const toggle = screen.getByRole("button", { name: "show the summary of 0:00" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    act(() => toggle.click());
    expect(screen.getByText("Jonas logs in through the gateway.")).toHaveClass("batch-summary");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    act(() => screen.getByRole("button", { name: "hide the summary of 0:00" }).click());
    expect(screen.queryByText("Jonas logs in through the gateway.")).toBeNull();
    expect(screen.queryByRole("button", { name: /summary of 2:00/ })).toBeNull();
  });

  it("lists the transcript in the panel, with or without timestamps, and a line's time plays from there", async () => {
    installServer({ [`GET /api${RUN_BASE}/transcript`]: { lines: [{ start_s: 42, end_s: 45, text: "Jonas opens the gateway." }] } });
    const onPlayFrom = vi.fn();
    const { unmount } = render(<RecordingView runBase={RUN_BASE} name="AcmeSync-demo.mp4" parts={ONE_PART}
      onPlayFrom={onPlayFrom} seekTo={null} onSeeked={vi.fn()} />);
    await screen.findByText("Jonas opens the gateway.");
    act(() => screen.getByRole("button", { name: "0:42" }).click());
    expect(onPlayFrom).toHaveBeenCalledWith(42);
    unmount();
    render(<RecordingView runBase={RUN_BASE} name="AcmeSync-demo.mp4" parts={ONE_PART} seekTo={null} onSeeked={vi.fn()}
      batches={[{ title: "Gateway login flow", start_s: 0, end_s: 120, summary: "" }]} />);
    act(() => screen.getByRole("tab", { name: "Transcript" }).click());
    await screen.findByText("Jonas opens the gateway.");
    act(() => screen.getByRole("button", { name: "0:42" }).click());
  });
});

describe("RecordingView audio", () => {
  it("plays an audio recording in an audio element, with no picture", () => {
    render(<RecordingView runBase="/runs/output/Jonas-weekly" name="Jonas-weekly.mp3"
      parts={[{ name: "Jonas-weekly.mp3", size_bytes: 1, start_s: 0, duration_s: 60 }]} onPlayFrom={() => {}}
      seekTo={null} onSeeked={() => {}} />);
    expect(document.querySelector("audio")).toBeInTheDocument();
    expect(document.querySelector("video")).not.toBeInTheDocument();
  });
});
