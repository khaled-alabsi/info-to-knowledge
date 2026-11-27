import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import VersionAskDialog from "./VersionAskDialog";

const RUNS = [
  { root: "output", name: "acmesync-guide", versions: [
    { number: 1, label: "markdown conversion (original)", path: "07_reshaping/earlier/1/before.md" },
    { number: 2, label: "after clean up", path: "07_reshaping/before.md" },
    { number: 3, label: "after rewrite (not finished)", path: "guide/guide.md" }] },
  { root: "output", name: "gateway-notes", versions: [
    { number: 1, label: "knowledge (original)", path: "07_reshaping/before.md" },
    { number: 2, label: "after knowledge reshaping", path: "notes/notes.md" }] }];

describe("VersionAskDialog", () => {
  it("shows a card a document with its versions as tags, its shown name, and sends the version chosen", async () => {
    const onChoose = vi.fn();
    render(<VersionAskDialog runs={RUNS} displayNames={{ "output/gateway-notes": "Gateway notes" }} onChoose={onChoose}
      onClose={() => undefined} />);
    expect(screen.getByText("2 documents have several versions.", { exact: false })).toBeInTheDocument();
    const guide = screen.getByRole("list", { name: "versions of acmesync-guide" }).querySelectorAll("li");
    expect([...guide].map((tag) => tag.className)).toEqual(["version-tag original", "version-tag", "version-tag unfinished"]);
    expect(guide[2]).toHaveTextContent("after rewritenot finished");
    const notes = screen.getByRole("list", { name: "versions of Gateway notes" }).querySelectorAll("li");
    expect(notes[1]).toHaveClass("latest");
    expect(notes[1]).toHaveTextContent("after knowledge reshapinglatest");
    expect(screen.getByText("3 versions")).toBeInTheDocument();
    for (const [name, version] of [["The original", "original"], ["All versions", "all"], ["The latest", "latest"]]) {
      await userEvent.click(screen.getByRole("button", { name }));
      expect(onChoose).toHaveBeenLastCalledWith(version);
    }
  });

  it("says one document when one has several versions", () => {
    render(<VersionAskDialog runs={RUNS.slice(1)} displayNames={{}} onChoose={() => undefined} onClose={() => undefined} />);
    expect(screen.getByText("1 document has several versions.", { exact: false })).toBeInTheDocument();
  });
});
