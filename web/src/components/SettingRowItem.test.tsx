import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SettingRow } from "../api";
import SettingRowItem from "./SettingRowItem";

const KEY_ROW = {
  key: "models.frame.apiKey", path: ["models", "frame", "apiKey"], value: null, source: "code", kind: "text",
  comment: "", resolved: null, resolved_note: "no key is needed: a placeholder is sent",
} as unknown as SettingRow;

const COUNT_ROW = {
  key: "models.frame.framesPerCall", path: ["models", "frame", "framesPerCall"], value: null, source: "code",
  kind: "number", comment: "", resolved: 4, resolved_note: "from the frames processing setting",
} as unknown as SettingRow;

describe("SettingRowItem", () => {
  it("puts only the short run value in an empty box and the note under it defect", () => {
    // Defect as shown: an empty box's placeholder held the value with its note, "no key is needed: a placeholder is
    // ser", cut off at 390 px. The fix: the placeholder is the value alone, the note a grey line under the box
    const context = { rows: [KEY_ROW, COUNT_ROW], drafts: {}, profile: "", edit: vi.fn(), typedKeys: [], setTyped: vi.fn() };
    render(<><SettingRowItem row={KEY_ROW} context={context} />
      <SettingRowItem row={COUNT_ROW} context={context} /></>);
    const boxes = screen.getAllByRole("textbox");
    expect(boxes.map((box) => box.getAttribute("placeholder"))).toEqual(["automatic", "4"]);
    expect(screen.getByText("no key is needed: a placeholder is sent")).toBeInTheDocument();
    expect(screen.getByText("4 (from the frames processing setting)")).toBeInTheDocument();
  });
});
