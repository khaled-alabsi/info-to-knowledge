import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { installServer } from "../test/fakeServer";
import RangeEditor, { buildInboxPreviewSource } from "./RangeEditor";
import type { RangeValue } from "./RangeEditor";

const NONE: RangeValue = { range_start: "", range_end: "", range_pages: "" };

// the editor as the run form holds it: the value kept by its parent, each change reported
function Holder({ durationS, pageCount, isAudio = false, playerS = 0, onSeek = () => undefined, onValue }: {
  durationS: number; pageCount: number; isAudio?: boolean; playerS?: number; onSeek?: (seconds: number) => void;
  onValue: (value: RangeValue) => void }) {
  const [value, setValue] = useState(NONE);
  return <RangeEditor source={buildInboxPreviewSource("inbox/acmesync.mp4")} durationS={durationS} pageCount={pageCount} isAudio={isAudio}
    value={value} onChange={(next) => { setValue(next); onValue(next); }} playerS={playerS} onSeek={onSeek}
    onClose={() => undefined} />;
}

describe("RangeEditor", () => {
  it("moves a handle with the keys, to either end, and a handle back at an edge sends nothing for it", async () => {
    const onValue = vi.fn();
    render(<Holder durationS={600} pageCount={0} onValue={onValue} />);
    const start = screen.getByRole("slider", { name: "start of processing" });
    fireEvent.keyDown(start, { key: "ArrowRight" });
    expect(onValue).toHaveBeenLastCalledWith({ range_start: "0:01", range_end: "", range_pages: "" });
    fireEvent.keyDown(start, { key: "ArrowUp", shiftKey: true });
    expect(onValue).toHaveBeenLastCalledWith({ range_start: "0:11", range_end: "", range_pages: "" });
    fireEvent.keyDown(start, { key: "ArrowDown", shiftKey: true });
    fireEvent.keyDown(start, { key: "ArrowLeft" });
    expect(onValue).toHaveBeenLastCalledWith(NONE);
    const end = screen.getByRole("slider", { name: "end of processing" });
    fireEvent.keyDown(end, { key: "Home" });
    expect(onValue).toHaveBeenLastCalledWith({ range_start: "", range_end: "0:01", range_pages: "" });
    fireEvent.keyDown(end, { key: "End" });
    expect(onValue).toHaveBeenLastCalledWith(NONE);
    onValue.mockClear();
    fireEvent.keyDown(end, { key: "a" });
    expect(onValue).not.toHaveBeenCalled();
  });

  it("takes the player's second for either end, and a click on the strip moves the player there", async () => {
    const onValue = vi.fn();
    const onSeek = vi.fn();
    render(<Holder durationS={600} pageCount={0} playerS={90} onSeek={onSeek} onValue={onValue} />);
    await userEvent.click(screen.getByRole("button", { name: /Start at 1:30/ }));
    expect(onValue).toHaveBeenLastCalledWith({ range_start: "1:30", range_end: "", range_pages: "" });
    await userEvent.click(screen.getByRole("button", { name: /End at 1:30/ }));
    expect(onValue).toHaveBeenLastCalledWith({ range_start: "1:30", range_end: "1:31", range_pages: "" });
    fireEvent.pointerDown(screen.getByLabelText("the recording's length"), { clientX: 0 });
    expect(onSeek).toHaveBeenCalled();
    // a handle dragged follows the pointer and shows the frame it stands on
    const handle = screen.getByRole("slider", { name: "start of processing" });
    fireEvent.pointerDown(handle, { clientX: 0, pointerId: 1 });
    fireEvent.pointerMove(handle, { clientX: 0, pointerId: 1 });
    fireEvent.pointerUp(handle, { pointerId: 1 });
    expect(onSeek).toHaveBeenCalledTimes(2);
  });

  it("refuses a typed time that is no time, past the end or the wrong way round, under the fields", async () => {
    render(<Holder durationS={600} pageCount={0} onValue={() => undefined} />);
    const endBox = screen.getByRole("textbox", { name: "end time" });
    await userEvent.clear(endBox);
    await userEvent.type(endBox, "20:00{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("The recording ends at 10:00.");
    await userEvent.clear(endBox);
    await userEvent.type(endBox, "late{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("Write the end as m:ss or h:mm:ss.");
    await userEvent.clear(endBox);
    await userEvent.type(endBox, "0:00{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("The end must come after the start, 0:00.");
    const startBox = screen.getByRole("textbox", { name: "start time" });
    await userEvent.clear(startBox);
    await userEvent.type(startBox, "x{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("Write the start as m:ss or h:mm:ss.");
  });

  it("draws an audio recording's waveform, and says so when the length cannot be read", async () => {
    installServer({ "GET /api/inbox/preview/waveform?path=inbox%2Facmesync.mp4&bins=160": { peaks: [0, 0.5, 1] } });
    const { container, unmount } = render(<Holder durationS={90} pageCount={0} isAudio onValue={() => undefined} />);
    expect(await screen.findByText(/Processing/)).toBeInTheDocument();
    await vi.waitFor(() => expect(container.querySelectorAll(".range-wave rect")).toHaveLength(3));
    unmount();
    render(<Holder durationS={0} pageCount={0} onValue={() => undefined} />);
    expect(screen.getByText("The recording's length could not be read, so no part of it can be chosen.")).toBeInTheDocument();
  });

  it("chooses a PDF's pages by click, shift-click and the fields, and refuses pages it does not have", async () => {
    const onValue = vi.fn();
    render(<Holder durationS={0} pageCount={10} onValue={onValue} />);
    const pages = screen.getAllByRole("option");
    await userEvent.click(pages[2]);
    expect(onValue).toHaveBeenLastCalledWith({ range_start: "", range_end: "", range_pages: "3-10" });
    fireEvent.click(pages[5], { shiftKey: true });
    expect(onValue).toHaveBeenLastCalledWith({ range_start: "", range_end: "", range_pages: "3-6" });
    fireEvent.click(pages[0], { shiftKey: true });
    expect(onValue).toHaveBeenLastCalledWith({ range_start: "", range_end: "", range_pages: "1-3" });
    const last = screen.getByRole("spinbutton", { name: "last page" });
    await userEvent.clear(last);
    await userEvent.type(last, "12{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("The pages run from 1 to 10.");
    const first = screen.getByRole("spinbutton", { name: "first page" });
    await userEvent.clear(first);
    await userEvent.type(first, "8");
    await userEvent.clear(last);
    await userEvent.type(last, "4{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("The last page must come after the first.");
    await userEvent.clear(first);
    await userEvent.type(first, "1");
    await userEvent.clear(last);
    await userEvent.type(last, "10{Enter}");
    expect(onValue).toHaveBeenLastCalledWith(NONE);
    await userEvent.click(screen.getByRole("button", { name: /^Whole/ }));
  });
});
