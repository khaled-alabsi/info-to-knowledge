import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { CompressibleVideo } from "../api";
import { installServer } from "../test/fakeServer";
import CompressView from "./CompressView";

function makeVideo(name: string, extra: Partial<CompressibleVideo> = {}): CompressibleVideo {
  return { root: "output", name, display_name: "", video: `${name}.mp4`, is_in_inbox: false, size_bytes: 300e6,
    state: "not compressed", progress: 0, error: "", before_bytes: 0, after_bytes: 0, ...extra };
}

const VIDEOS = [
  makeVideo("Gateway-review"),
  makeVideo("AcmeSync-kickoff", { is_in_inbox: true }),
  makeVideo("Jonas-onboarding", { state: "compressed", before_bytes: 300e6, after_bytes: 90e6 }),
];

describe("CompressView", () => {
  it("ticks all, none or the inverse of the videos not compressed yet, and compresses the ticked ones", async () => {
    const calls = installServer({ "GET /api/videos/compression": VIDEOS, "POST /api/videos/compression": { pid: 42 } });
    const onMessage = vi.fn();
    render(<CompressView onBack={() => {}} onMessage={onMessage} />);
    expect(await screen.findByText("Gateway-review")).toBeInTheDocument();
    // a compressed video stays listed with its sizes and cannot be ticked again (the owner)
    expect(screen.getByText("compressed · 300 MB → 90 MB")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "tick Jonas-onboarding" })).toBeDisabled();
    expect(screen.getByText(/in the inbox, kept in its run folder first/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Compress" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "All 2" }));
    expect(screen.getByRole("button", { name: "Compress 2 · 600 MB" })).toBeEnabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "tick AcmeSync-kickoff" }));
    await userEvent.click(screen.getByRole("button", { name: "Invert" }));
    expect(screen.getByRole("checkbox", { name: "tick AcmeSync-kickoff" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "tick Gateway-review" })).not.toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "None" }));
    expect(screen.getByRole("button", { name: "Compress" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "All 2" }));
    await userEvent.click(screen.getByRole("button", { name: /Compress 2/ }));
    expect(calls.find((call) => call.method === "POST")?.body).toEqual({ runs: [
      { root: "output", name: "Gateway-review" }, { root: "output", name: "AcmeSync-kickoff" }] });
    expect(onMessage).toHaveBeenCalledWith("Compressing 2 videos, one after another", false);
  });

  it("while a compression runs, no video can start another: the waiting and compressing ones cannot be ticked", async () => {
    installServer({ "GET /api/videos/compression": [makeVideo("Gateway-review", { state: "compressing", progress: 0.42 }),
      makeVideo("AcmeSync-kickoff", { state: "waiting" }), makeVideo("Jonas-onboarding")] });
    render(<CompressView onBack={() => {}} onMessage={() => {}} />);
    expect(await screen.findByText("compressing 42%")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "tick Gateway-review" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "tick AcmeSync-kickoff" })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "tick Jonas-onboarding" }));
    expect(screen.getByRole("button", { name: /Compress 1/ })).toBeDisabled();
  });
});
