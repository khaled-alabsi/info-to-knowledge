import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { installServer } from "../test/fakeServer";
import AnotherPartDialog from "./AnotherPartDialog";

const BASE = "/runs/output/acmesync-guide";

describe("AnotherPartDialog", () => {
  it("starts from the run's own part, refuses to send it again, and sends the pages chosen", async () => {
    installServer({ [`GET /api${BASE}/input`]: { recording: "guide.pdf", is_audio: false, duration_s: 0, page_count: 8,
      range_start: "", range_end: "", range_pages: "2-4" } });
    const onSubmit = vi.fn();
    render(<AnotherPartDialog runBase={BASE} runName="acmesync-guide" onSubmit={onSubmit} onClose={() => undefined} />);

    expect(await screen.findByText(/Now: pages 2 to 4\./)).toBeInTheDocument();
    const process = screen.getByRole("button", { name: /^Process/ });
    expect(process).toBeDisabled();
    await userEvent.click(screen.getAllByRole("option")[5]);
    expect(process).toHaveTextContent("Process page 6");
    await userEvent.click(process);
    expect(onSubmit).toHaveBeenCalledWith({ range_start: "", range_end: "", range_pages: "6" });
  });

  it("names a whole recording, and says why when the run's input cannot be read", async () => {
    installServer({ [`GET /api${BASE}/input`]: { recording: "call.m4a", is_audio: true, duration_s: 90, page_count: 0,
      range_start: "", range_end: "", range_pages: "" } });
    const { unmount } = render(<AnotherPartDialog runBase={BASE} runName="acmesync-call" onSubmit={() => undefined}
      onClose={() => undefined} />);
    expect(await screen.findByText(/Now: whole recording\./)).toBeInTheDocument();
    unmount();
    installServer({});
    render(<AnotherPartDialog runBase={BASE} runName="acmesync-call" onSubmit={() => undefined} onClose={() => undefined} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/no route/);
  });
});
