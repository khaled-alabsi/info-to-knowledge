import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Dialog from "./Dialog";

describe("Dialog", () => {
  it("closes once on Escape, which the browser reports as a cancel and then a close", () => {
    const onClose = vi.fn();
    render(<Dialog title="Delete permanently?" onClose={onClose}><p>body</p></Dialog>);
    const dialog = screen.getByRole("dialog");
    // the browser's Escape: a cancel event whose default action closes the dialog, which fires close
    if (fireEvent(dialog, new Event("cancel", { cancelable: true }))) (dialog as HTMLDialogElement).close();
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
