import { render, screen, within } from "@testing-library/react";
// `within` reads the all-tags window below
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { answerJson, installServer, makeRun } from "../test/fakeServer";
import DownloadView from "./DownloadView";

const RUNS = [
  makeRun({ name: "Gateway-review", has_document: true, tags: ["client Acme", "demo"] }),
  makeRun({ name: "Jonas-call", has_document: true, tags: ["demo"] }),
  makeRun({ name: "AcmeSync-draft", has_document: false, tags: ["client Acme"] }),
];

function serve(download = () => new Response("zip", {
  headers: { "Content-Disposition": "attachment; filename*=UTF-8''documents-2.zip", "X-Skipped-Runs": "" } })) {
  return installServer({
    "GET /api/download/parts": [{ key: "document", label: "Final document with its resources folder" }],
    "POST /api/download/documents": download,
  });
}

describe("DownloadView", () => {
  it("says when what can be downloaded cannot be read", async () => {
    installServer({ "GET /api/download/parts": answerJson({ detail: "down" }, 500) });
    const onMessage = vi.fn();
    render(<DownloadView runs={RUNS} onBack={() => {}} onMessage={onMessage} />);
    await vi.waitFor(() => expect(onMessage).toHaveBeenCalledWith("What can be downloaded could not be read: 500: down", true));
  });

  it("filters by a tag chosen from a box that suggests only the runs' tags, never every tag as a chip defect", async () => {
    // Every tag of every run stood as a chip, about 150, and filled the view. Now one box suggests the runs' tags as
    // they are typed, and a word that is no run's tag adds nothing.
    serve();
    render(<DownloadView runs={RUNS} onBack={() => {}} onMessage={() => {}} />);
    expect(await screen.findByText("2 runs with a document")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "demo" })).not.toBeInTheDocument();
    const box = screen.getByRole("combobox", { name: "filter by tag" });
    await userEvent.type(box, "dem");
    expect(screen.getByRole("option", { name: "demo" })).toBeInTheDocument();
    await userEvent.clear(box);
    await userEvent.type(box, "nobody{Enter}");
    expect(screen.queryByRole("button", { name: /remove tag/ })).not.toBeInTheDocument();

    // beside the box, every tag in a window to tick by looking
    await userEvent.click(screen.getByRole("button", { name: "All tags (2)" }));
    await userEvent.click(within(screen.getByRole("group", { name: "every tag" })).getByRole("button", { name: "demo" }));
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(screen.getByRole("button", { name: "remove tag demo" })).toBeInTheDocument();
  });

  it("filters the runs with a document by tag, and downloads the ticked ones", async () => {
    const calls = serve();
    const onMessage = vi.fn();
    render(<DownloadView runs={RUNS} onBack={() => {}} onMessage={onMessage} />);
    expect(screen.getByRole("heading", { name: "Download" })).toBeInTheDocument();
    // one part to download is no choice: no box offers it
    expect(await screen.findByText("2 runs with a document")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: "Final document with its resources folder" })).not.toBeInTheDocument();
    expect(screen.queryByText("AcmeSync-draft")).not.toBeInTheDocument();

    await userEvent.type(screen.getByRole("combobox", { name: "filter by tag" }), "acm{Enter}");
    expect(screen.queryByText("Jonas-call")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "remove tag client Acme" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "tick every run shown" }));
    await userEvent.click(screen.getByRole("button", { name: /Download 2/ }));
    expect(calls.find((call) => call.url === "/api/download/documents")?.body).toEqual({
      runs: [{ root: "knowledge", name: "Gateway-review" }, { root: "knowledge", name: "Jonas-call" }], parts: ["document"],
      version: "" });
    expect(onMessage).toHaveBeenCalledWith("Downloaded 2 runs");
  });

  it("names a run left out and says why a download failed", async () => {
    let answer: Response = new Response("zip", { headers: { "X-Skipped-Runs": "Jonas-call" } });
    serve(() => answer);
    const onMessage = vi.fn();
    render(<DownloadView runs={RUNS} onBack={() => {}} onMessage={onMessage} />);
    await userEvent.click(await screen.findByText("Gateway-review"));
    await userEvent.click(screen.getByText("Jonas-call"));
    await userEvent.click(screen.getByRole("button", { name: /Download 2/ }));
    expect(onMessage).toHaveBeenLastCalledWith("Downloaded 1 run; left out, no document: Jonas-call");
    answer = answerJson({ detail: "none of the ticked runs has a finished document" }, 409);
    await userEvent.click(screen.getByRole("button", { name: /Download 2/ }));
    expect(onMessage).toHaveBeenLastCalledWith("Download failed: 409: none of the ticked runs has a finished document", true);
  });

  it("finds a run by name in the list", async () => {
    serve();
    render(<DownloadView runs={RUNS} onBack={() => {}} onMessage={vi.fn()} />);
    await userEvent.type(await screen.findByRole("searchbox", { name: "find a run" }), "jonas");
    expect(screen.getByText("Jonas-call")).toBeInTheDocument();
    expect(screen.queryByText("Gateway-review")).not.toBeInTheDocument();
  });
});
