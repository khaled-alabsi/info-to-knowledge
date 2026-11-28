import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { InboxItem, Job, JobRequest, RunSummary } from "../api";
import { FakeXMLHttpRequest, PHASES, answerJson, installServer, makeInboxItem, makeJob, makeRun } from "../test/fakeServer";
import { readViewValue } from "../viewState";
import InboxPanel from "./InboxPanel";

const NEW_ITEM = makeInboxItem("Gateway.mp4", "new", { size_bytes: 1_500_000_000 });
const STARTED_ITEM = makeInboxItem("Jonas-call.mp4", "started", { size_bytes: 900 });
const DONE_ITEM = makeInboxItem("AcmeSync-demo.mp4", "done");

function renderInbox(items: InboxItem[] = [NEW_ITEM, STARTED_ITEM, DONE_ITEM], runs: RunSummary[] = [], jobs: Job[] = []) {
  const onOpenRun = vi.fn();
  const onChanged = vi.fn();
  const onMessage = vi.fn();
  const onOpenJob = vi.fn();
  const onOpenVideo = vi.fn();
  const view = render(<InboxPanel items={items} phases={PHASES} runs={runs} jobs={jobs} openKey="" onOpenRun={onOpenRun}
    onOpenJob={onOpenJob} onOpenVideo={onOpenVideo} onChanged={onChanged} onMessage={onMessage} />);
  return { ...view, onOpenRun, onOpenJob, onOpenVideo, onChanged, onMessage };
}

function openDone() {
  return userEvent.click(screen.getByRole("button", { name: /^Done/ }));
}

function tick(name: string) {
  return userEvent.click(screen.getByText(name).closest("label")!.querySelector("input")!);
}

// A drag's data as the page sets and reads it (jsdom has no DataTransfer of its own)
function makeDataTransfer() {
  const data: Record<string, string> = {};
  return { setData: (type: string, value: string) => { data[type] = value; }, getData: (type: string) => data[type] ?? "",
    get types() { return Object.keys(data); } };
}

describe("InboxPanel own lists", () => {
  it("shows a list's rows with their status pills, out of their status section, and drags a row out and in", async () => {
    const saved: unknown[] = [];
    installServer({ "GET /api/inbox/trash": [], "GET /api/lists": { lists: [{ id: "a", name: "Review later", members: [NEW_ITEM.video] }] },
      "PUT /api/lists": (body: { lists: unknown[] }) => { saved.push(body.lists); return { lists: body.lists }; } });
    renderInbox();
    const list = await screen.findByRole("button", { name: /^Review later/ });
    if (list.getAttribute("aria-expanded") !== "true") await userEvent.click(list);
    // the new video stands in the list with its pill, and its New section is gone with it
    expect(screen.queryByRole("button", { name: /^New/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^new ›/ })).toBeInTheDocument();
    // a list holding a row cannot be deleted; dragged out onto Not finished, the row leaves it and it can be
    expect(screen.queryByRole("button", { name: "delete the list Review later" })).not.toBeInTheDocument();
    const transfer = makeDataTransfer();
    fireEvent.dragStart(screen.getByText("Gateway.mp4").closest("li")!, { dataTransfer: transfer });
    const unfinished = screen.getByRole("button", { name: /^Not finished/ }).closest(".section-head")!;
    fireEvent.dragOver(unfinished, { dataTransfer: transfer });
    fireEvent.drop(unfinished, { dataTransfer: transfer });
    expect(await screen.findByRole("button", { name: "delete the list Review later" })).toBeInTheDocument();
    expect(saved.at(-1)).toEqual([{ id: "a", name: "Review later", members: [] }]);
    // dragged back in
    const back = makeDataTransfer();
    fireEvent.dragStart(screen.getByText("Gateway.mp4").closest("li")!, { dataTransfer: back });
    const head = screen.getByRole("button", { name: /^Review later/ }).closest(".section-head")!;
    fireEvent.dragOver(head, { dataTransfer: back });
    fireEvent.drop(head, { dataTransfer: back });
    expect(saved.at(-1)).toEqual([{ id: "a", name: "Review later", members: [NEW_ITEM.video] }]);
  });

  it("shows a done row of a list under Done too, and keeps it in its list defect", async () => {
    // A row put in a list left Done once its processing ended, so a finished run was in the list alone.
    // Now a done row stands in Done and stays in its list; a row not done still stands in its list alone.
    installServer({ "GET /api/inbox/trash": [], "GET /api/lists": { lists: [{ id: "a", name: "Review later",
      members: [DONE_ITEM.video, STARTED_ITEM.video] }] }, "PUT /api/lists": (body: { lists: unknown[] }) => ({ lists: body.lists }) });
    renderInbox();
    const list = await screen.findByRole("button", { name: /^Review later/ });
    if (list.getAttribute("aria-expanded") !== "true") await userEvent.click(list);
    await openDone();
    expect(screen.getAllByText("AcmeSync-demo.mp4")).toHaveLength(2);
    expect(screen.getAllByText("Jonas-call.mp4")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: /^Not finished/ })).not.toBeInTheDocument();
  });

  it("orders a list's rows by the status their pill shows, queued before stopped before new, and done at the bottom", async () => {
    const changed = makeInboxItem("AcmeSync-demo.mp4", "done", { state_at: 1000 });
    const finished = makeInboxItem("Gateway.mp4", "done", { state_at: 4000 });
    const started = makeInboxItem("Jonas-call.mp4", "started", { state_at: 2000 });
    const fresh = makeInboxItem("Weekly.mp4", "new", { state_at: 3000 });
    const change = makeJob({ status: "queued" }, { video: changed.video, output_root: changed.output, only_phase: 7, document_pass: "knowledge" });
    installServer({ "GET /api/inbox/trash": [], "GET /api/lists": { lists: [{ id: "a", name: "Review later",
      members: [finished.video, fresh.video, started.video, changed.video] }] },
      "PUT /api/lists": (body: { lists: unknown[] }) => ({ lists: body.lists }) });
    renderInbox([changed, finished, started, fresh], [], [change]);
    const list = await screen.findByRole("button", { name: /^Review later/ });
    if (list.getAttribute("aria-expanded") !== "true") await userEvent.click(list);
    const rows = [...document.querySelectorAll(".section-list .rows li")].map((row) => row.querySelector(".row-name")!.textContent);
    expect(rows).toEqual(["AcmeSync-demo.mp4", "Jonas-call.mp4", "Weekly.mp4", "Gateway.mp4"]);
  });

  it("keeps a list's video in the list as its run folder once its recording moved there defect", async () => {
    // A PDF put in a list was gone from it once its run was done: the recording moved into the run folder, the inbox
    // listed it no more, and the list held only its inbox path. Now the list holds the run folder by that path.
    const video = "/work/inbox/Gateway-guide.pdf";
    const saved: unknown[] = [];
    installServer({ "GET /api/inbox/trash": [], "GET /api/lists": { lists: [{ id: "a", name: "Review later", members: [video] }] },
      "PUT /api/lists": (body: { lists: unknown[] }) => { saved.push(body.lists); return { lists: body.lists }; } });
    renderInbox([NEW_ITEM], [makeRun({ name: "Gateway-guide", video, status: "complete", has_document: true })]);
    const list = await screen.findByRole("button", { name: /^Review later/ });
    if (list.getAttribute("aria-expanded") !== "true") await userEvent.click(list);
    expect(within(list.closest(".inbox-section, section, div")!.parentElement!).getAllByText("Gateway-guide").length).toBeGreaterThan(0);
    // dragged out onto New, it leaves the list whole, its inbox path with it
    const transfer = makeDataTransfer();
    fireEvent.dragStart(screen.getAllByText("Gateway-guide")[0].closest("li")!, { dataTransfer: transfer });
    const head = screen.getByRole("button", { name: /^New/ }).closest(".section-head")!;
    fireEvent.dragOver(head, { dataTransfer: transfer });
    fireEvent.drop(head, { dataTransfer: transfer });
    expect(saved.at(-1)).toEqual([{ id: "a", name: "Review later", members: [] }]);
  });

  it("downloads a list's ticked done documents as one zip, the button active only while they are ticked defect", async () => {
    // The list's download button showed with nothing ticked and downloaded every done row unasked; then it was hidden
    // instead. Now it is always there, inactive until done rows alone are ticked (the owner: "make it inactive").
    const calls = installServer({ "GET /api/inbox/trash": [], "GET /api/lists": { lists: [{ id: "a", name: "Review later",
      members: [DONE_ITEM.video, NEW_ITEM.video] }] }, "PUT /api/lists": (body: { lists: unknown[] }) => ({ lists: body.lists }),
      // one version of the document, so the download asks nothing
      "POST /api/download/document-versions": [{ root: DONE_ITEM.runs[0].root, name: DONE_ITEM.runs[0].name,
        versions: [{ number: 1, label: "original", path: "a/a.md" }] }],
      "POST /api/download/documents": () => new Response("zip", {
        headers: { "Content-Disposition": "attachment; filename*=UTF-8''documents-1.zip", "X-Skipped-Runs": "" } }) });
    renderInbox();
    const list = await screen.findByRole("button", { name: /^Review later/ });
    if (list.getAttribute("aria-expanded") !== "true") await userEvent.click(list);
    const download = () => screen.getByRole("button", { name: "download from the list Review later" });
    expect(download()).toBeDisabled();
    await tick("AcmeSync-demo.mp4");
    expect(download()).toBeEnabled();
    // a ticked row not done makes it inactive, never hides it
    await tick("Gateway.mp4");
    expect(download()).toBeDisabled();
    await tick("Gateway.mp4");
    await userEvent.click(screen.getByRole("button", { name: "download from the list Review later" }));
    await waitFor(() => expect(calls.find((call) => call.url === "/api/download/documents")?.body).toEqual({
      runs: [{ root: DONE_ITEM.runs[0].root, name: DONE_ITEM.runs[0].name }], parts: ["document"], version: "" }));
  });

  it("moves every ticked row when one of them is dragged onto a list defect", async () => {
    // Several rows ticked and one dragged onto a list moved only the one held
    // Now a ticked row carries every ticked row with it, and they are unticked once moved
    const saved: unknown[] = [];
    installServer({ "GET /api/inbox/trash": [], "GET /api/lists": { lists: [{ id: "a", name: "Review later", members: [] }] },
      "PUT /api/lists": (body: { lists: unknown[] }) => { saved.push(body.lists); return { lists: body.lists }; } });
    renderInbox();
    await screen.findByRole("button", { name: /^Review later/ });
    await tick("Gateway.mp4");
    await tick("Jonas-call.mp4");
    const transfer = makeDataTransfer();
    fireEvent.dragStart(screen.getByText("Gateway.mp4").closest("li")!, { dataTransfer: transfer });
    const head = screen.getByRole("button", { name: /^Review later/ }).closest(".section-head")!;
    fireEvent.dragOver(head, { dataTransfer: transfer });
    fireEvent.drop(head, { dataTransfer: transfer });
    expect(saved.at(-1)).toEqual([{ id: "a", name: "Review later", members: [NEW_ITEM.video, STARTED_ITEM.video] }]);
    expect(screen.queryByText(/ticked/)).not.toBeInTheDocument();
  });

  it("skips the dragged rows a list already holds, keeping their place, with no message defect", async () => {
    // Rows ticked inside a list and outside it, dropped on that list, moved its own rows to its end
    // Now a row the list holds keeps its place and only the others are added, with no message
    const saved: unknown[] = [];
    installServer({ "GET /api/inbox/trash": [], "GET /api/inbox/archive": [], "GET /api/lists": { lists: [{ id: "a", name: "Review later",
      members: [NEW_ITEM.video, DONE_ITEM.video] }] },
      "PUT /api/lists": (body: { lists: unknown[] }) => { saved.push(body.lists); return { lists: body.lists }; } });
    const { onMessage } = renderInbox();
    const list = await screen.findByRole("button", { name: /^Review later/ });
    if (list.getAttribute("aria-expanded") !== "true") await userEvent.click(list);
    await tick("Gateway.mp4");
    await tick("Jonas-call.mp4");
    const transfer = makeDataTransfer();
    fireEvent.dragStart(screen.getByText("Jonas-call.mp4").closest("li")!, { dataTransfer: transfer });
    const head = screen.getByRole("button", { name: /^Review later/ }).closest(".section-head")!;
    fireEvent.dragOver(head, { dataTransfer: transfer });
    fireEvent.drop(head, { dataTransfer: transfer });
    expect(saved.at(-1)).toEqual([{ id: "a", name: "Review later", members: [NEW_ITEM.video, DONE_ITEM.video, STARTED_ITEM.video] }]);
    expect(onMessage).not.toHaveBeenCalled();
  });

  it("adds the rows ticked in a list's + picker, which leaves out the rows already in it", async () => {
    const saved: unknown[] = [];
    installServer({ "GET /api/inbox/trash": [], "GET /api/lists": { lists: [{ id: "a", name: "Review later", members: [NEW_ITEM.video] }] },
      "PUT /api/lists": (body: { lists: unknown[] }) => { saved.push(body.lists); return { lists: body.lists }; } });
    renderInbox();
    await userEvent.click(await screen.findByRole("button", { name: "add rows to the list Review later" }));
    const picker = screen.getByRole("dialog");
    expect(within(picker).queryByRole("checkbox", { name: "pick Gateway.mp4" })).not.toBeInTheDocument();
    expect(within(picker).getByRole("button", { name: /Add/ })).toBeDisabled();
    await userEvent.click(within(picker).getByRole("button", { name: /^Done/ }));
    await userEvent.click(within(picker).getByRole("checkbox", { name: "pick AcmeSync-demo.mp4" }));
    await userEvent.click(within(picker).getByRole("button", { name: /Add 1/ }));
    expect(saved.at(-1)).toEqual([{ id: "a", name: "Review later", members: [NEW_ITEM.video, DONE_ITEM.video] }]);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("uploads through a list's own upload button into the inbox and into that list, and a plain upload into no list", async () => {
    const saved: unknown[] = [];
    installServer({ "GET /api/inbox/trash": [], "GET /api/lists": { lists: [{ id: "a", name: "Review later", members: [] }] },
      "PUT /api/lists": (body: { lists: unknown[] }) => { saved.push(body.lists); return { lists: body.lists }; } });
    const props = { phases: PHASES, runs: [], jobs: [], openKey: "", onOpenRun: vi.fn(), onOpenJob: vi.fn(),
      onOpenVideo: vi.fn(), onChanged: vi.fn(), onMessage: vi.fn() };
    const { container, rerender } = render(<InboxPanel items={[NEW_ITEM]} {...props} />);
    const picker = () => container.querySelector<HTMLInputElement>("input[type=file]")!;
    // a plain upload: saved into the inbox, into no list
    await userEvent.upload(picker(), new File(["x"], "Jonas-call.mp4", { type: "video/mp4" }));
    await act(async () => FakeXMLHttpRequest.latest().respond(200, [{ name: "Jonas-call.mp4", saved_as: "Jonas-call.mp4", error: "" }]));
    rerender(<InboxPanel items={[NEW_ITEM, STARTED_ITEM]} {...props} />);
    expect(saved).toEqual([]);
    // through the list's button: once the inbox lists the saved file, it stands in the list
    await userEvent.click(await screen.findByRole("button", { name: "upload into the list Review later" }));
    await userEvent.upload(picker(), new File(["x"], "AcmeSync-demo.mp4", { type: "video/mp4" }));
    await act(async () => FakeXMLHttpRequest.latest().respond(200, [{ name: "AcmeSync-demo.mp4",
      saved_as: "inbox/AcmeSync-demo.mp4", error: "" }]));
    rerender(<InboxPanel items={[NEW_ITEM, STARTED_ITEM, DONE_ITEM]} {...props} />);
    expect(saved.at(-1)).toEqual([{ id: "a", name: "Review later", members: [DONE_ITEM.video] }]);
  });

  it("groups the + picker's rows under the list or section they stand in, and filters them by a tag", async () => {
    const tagged = makeInboxItem("Gateway.mp4", "new", { tags: ["client"] });
    installServer({ "GET /api/inbox/trash": [], "GET /api/inbox/archive": [],
      "GET /api/lists": { lists: [{ id: "a", name: "Review later", members: [] }, { id: "b", name: "Clients", members: [DONE_ITEM.video] }] },
      "PUT /api/lists": (body: { lists: unknown[] }) => ({ lists: body.lists }) });
    renderInbox([tagged, STARTED_ITEM, DONE_ITEM]);
    await userEvent.click(await screen.findByRole("button", { name: "add rows to the list Review later" }));
    const picker = screen.getByRole("dialog");
    // every place starts folded, and opens on a click
    expect(within(picker).queryByText("Jonas-call.mp4")).not.toBeInTheDocument();
    for (const place of ["New", "Not finished", "Clients"]) await userEvent.click(within(picker).getByRole("button", { name: new RegExp(`^${place}`) }));
    expect(within(within(picker).getByRole("region", { name: "New" })).getByText("Gateway.mp4")).toBeInTheDocument();
    expect(within(within(picker).getByRole("region", { name: "Not finished" })).getByText("Jonas-call.mp4")).toBeInTheDocument();
    expect(within(within(picker).getByRole("region", { name: "Clients" })).getByText("AcmeSync-demo.mp4")).toBeInTheDocument();
    // a place's box picks every row in it, and picks none again
    await userEvent.click(within(picker).getByRole("checkbox", { name: "pick every row in Not finished" }));
    expect(within(picker).getByRole("checkbox", { name: "pick Jonas-call.mp4" })).toBeChecked();
    expect(within(picker).getByRole("button", { name: /Add 1/ })).toBeEnabled();
    await userEvent.click(within(picker).getByRole("checkbox", { name: "pick every row in Not finished" }));
    expect(within(picker).getByRole("checkbox", { name: "pick Jonas-call.mp4" })).not.toBeChecked();
    // the tag filter is a button beside the search, its tags hidden until opened, and a search narrows them
    expect(within(picker).queryByRole("button", { name: "client" })).not.toBeInTheDocument();
    await userEvent.click(within(picker).getByRole("button", { name: "filter by tag" }));
    await userEvent.type(within(picker).getByRole("searchbox", { name: "find a tag" }), "zzz");
    expect(within(picker).queryByRole("button", { name: "client" })).not.toBeInTheDocument();
    await userEvent.clear(within(picker).getByRole("searchbox", { name: "find a tag" }));
    await userEvent.click(within(picker).getByRole("button", { name: "client" }));
    expect(within(picker).getByText("Gateway.mp4")).toBeInTheDocument();
    expect(within(picker).queryByText("Jonas-call.mp4")).not.toBeInTheDocument();
    expect(within(picker).queryByRole("region", { name: "Clients" })).not.toBeInTheDocument();
    // the chosen tag stands under the search, and a click on it stops filtering
    await userEvent.click(within(picker).getByRole("button", { name: "stop filtering by client" }));
    expect(within(picker).getByRole("button", { name: /^Not finished/ })).toBeInTheDocument();
  });

  it("makes a list from the New list button and renames it", async () => {
    const saved: { name: string }[][] = [];
    installServer({ "GET /api/inbox/trash": [], "GET /api/lists": { lists: [] },
      "PUT /api/lists": (body: { lists: { name: string }[] }) => { saved.push(body.lists); return { lists: body.lists }; } });
    renderInbox();
    await userEvent.click(screen.getByRole("button", { name: "new list" }));
    await userEvent.type(screen.getByRole("textbox", { name: "name of the new list" }), "Clients{Enter}");
    expect(await screen.findByRole("button", { name: /^Clients/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "rename the list Clients" }));
    const box = screen.getByRole("textbox", { name: "new name of Clients" });
    await userEvent.clear(box);
    await userEvent.type(box, "Partners{Enter}");
    expect(saved.at(-1)?.[0].name).toBe("Partners");
  });
});

describe("InboxPanel list", () => {
  it("lists a section's videos and run folders together, newest first by the date their state means", async () => {
    const older = makeInboxItem("AcmeSync-demo.mp4", "done", { state_at: 1000 });
    const newer = makeInboxItem("Gateway.mp4", "done", { state_at: 3000 });
    const loose = makeRun({ root: "output", name: "Jonas-call", video: "", status: "complete", has_document: true, state_at: 2000 });
    renderInbox([older, newer], [loose]);
    await openDone();
    const rows = [...document.querySelectorAll(".rows li")].map((row) => row.querySelector(".row-name")!.textContent);
    expect(rows).toEqual(["Gateway.mp4", "Jonas-call", "AcmeSync-demo.mp4"]);
  });

  it("lists a video whose run was reset to 0 once, under New, and never its emptied run folder", () => {
    const reset = makeInboxItem("Weekly.mp4", "new", { empty_runs: [{ root: "output", name: "Weekly" }] });
    const emptied = makeRun({ root: "output", name: "Weekly", video: reset.video, status: "incomplete", state_at: 2000 });
    renderInbox([reset], [emptied]);
    const rows = [...document.querySelectorAll(".rows li")].map((row) => row.querySelector(".row-name")!.textContent);
    expect(rows).toEqual(["Weekly.mp4"]);
    expect(screen.queryByRole("button", { name: /^Not finished/ })).not.toBeInTheDocument();
  });

  it("lists the videos in New, Not finished and Done, which open and close, and says when the inbox is empty", async () => {
    const { rerender } = renderInbox();
    expect(screen.getByRole("button", { name: /^New/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /^Not finished/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /^Done/ })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: /^Running/ })).not.toBeInTheDocument();
    expect(screen.queryByText("AcmeSync-demo.mp4")).not.toBeInTheDocument();
    expect([...document.querySelectorAll(".section-toggle .count")].map((count) => count.textContent)).toEqual(["1", "1", "1"]);
    expect(screen.getByText("1.5 GB")).toBeInTheDocument();
    expect(screen.getByText("1 KB")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^new ›/ })).toHaveClass("pill", "new");
    expect(screen.getByRole("button", { name: /^not finished ›/ })).toHaveClass("incomplete");
    await openDone();
    expect(screen.getByText("250 MB")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^done ›/ })).toHaveClass("complete");
    await userEvent.click(screen.getByRole("button", { name: /^New/ }));
    expect(screen.queryByText("Gateway.mp4")).not.toBeInTheDocument();
    rerender(<InboxPanel items={[]} phases={PHASES} runs={[]} jobs={[]} openKey="" onOpenRun={vi.fn()} onOpenJob={vi.fn()} onOpenVideo={vi.fn()} onChanged={vi.fn()} onMessage={vi.fn()} />);
    expect(screen.getByText("The inbox is empty. Drop a recording above.")).toBeInTheDocument();
  });

  it("keeps which sections are open in the view cookie", async () => {
    const first = renderInbox();
    await openDone();
    await userEvent.click(screen.getByRole("button", { name: /^New/ }));
    expect(readViewValue("inboxSections", [])).toEqual(["running", "unfinished", "done"]);
    first.unmount();
    renderInbox();
    expect(screen.getByRole("button", { name: /^Done/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("button", { name: /^New/ })).toHaveAttribute("aria-expanded", "false");
  });

  it("puts a run folder no inbox video owns in the section of its state, as a row that opens it", async () => {
    const owned = makeInboxItem("Gateway.mp4", "running", { runs: [
      { root: "output", name: "Gateway", status: "running", is_live: true, has_document: false, topic: "", display_name: "", stopped_phase: 0 }] });
    const runs = [makeRun({ root: "output", name: "Gateway", status: "running", is_live: true }),
      makeRun({ root: "output", name: "Acme-live", status: "running", is_live: true }),
      makeRun({ root: "knowledge", name: "Jonas-old", status: "complete" }),
      makeRun({ root: "output", name: "Acme-stopped", status: "incomplete" })];
    const { onOpenRun } = renderInbox([owned], runs);
    expect(screen.queryByRole("button", { name: /^Other runs/ })).not.toBeInTheDocument();
    expect([...document.querySelectorAll(".section-toggle .count")].map((count) => count.textContent)).toEqual(["2", "1", "1"]);
    const liveRow = screen.getByRole("button", { name: /Acme-live/ });
    expect(liveRow).not.toHaveTextContent(/output/);
    expect(within(liveRow).queryByRole("button", { name: /start a run|resume the run/ })).toBeNull();
    expect(liveRow.querySelector(".dot")).toBeNull();
    expect(screen.getByRole("button", { name: /Acme-stopped/ })).toHaveTextContent("not finished ›");
    expect(screen.getByRole("button", { name: /Acme-live/ })).toHaveTextContent("running ›");
    expect(screen.queryByRole("button", { name: /^Gateway/ })).not.toBeInTheDocument();
    await openDone();
    expect(screen.getByRole("button", { name: /Jonas-old/ })).toHaveTextContent("done ›");
    expect(screen.getByRole("button", { name: /Jonas-old/ }).querySelector(".pill")).toHaveClass("complete");
    await userEvent.click(screen.getByRole("button", { name: /Jonas-old/ }));
    expect(onOpenRun).toHaveBeenCalledWith("knowledge", "Jonas-old");
    expect(screen.getByRole("checkbox", { name: "select every video in Done" })).toBeEnabled();
  });

  it("shows one row for run folders of one video: the run that went furthest, never an abandoned earlier one", async () => {
    const video = "/work/inbox/Jonas-weekly.mp4";
    const runs = [makeRun({ root: "output", name: "Jonas-weekly", video, status: "incomplete", state_at: 1000 }),
      makeRun({ root: "output", name: "Jonas-weekly-r2", video, status: "complete", has_document: true, state_at: 2000 }),
      makeRun({ root: "output", name: "Jonas-weekly-r3", video, status: "incomplete", state_at: 3000 })];
    renderInbox([], runs);
    expect(screen.queryByRole("button", { name: /Not finished/ })).not.toBeInTheDocument();
    await openDone();
    expect(screen.getByRole("button", { name: /Jonas-weekly-r2/ })).toHaveTextContent("done ›");
    expect(screen.queryByRole("button", { name: /^Jonas-weekly\b(?!-)/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Jonas-weekly-r3/ })).not.toBeInTheDocument();
  });

  it("names each running video's provider set in its pill while runs go on more than one set, and none with one set defect", () => {
    // Defect: three videos ran on three provider sets and each pill said only "running ›", so the owner could not see
    // which machine ran which. Fix: with more than one set running, the pill says "running · Mac ›".
    const runningOn = (name: string, providerSet: string) => makeInboxItem(`${name}.mp4`, "running", { runs: [
      { root: "output", name, status: "running", is_live: true, has_document: false, topic: "", display_name: "", stopped_phase: 0,
        provider_set: providerSet }] });
    const runs = [makeRun({ root: "output", name: "Gateway", is_live: true, status: "running", provider_set: "Mac" }),
      makeRun({ root: "output", name: "Weekly", is_live: true, status: "running", provider_set: "" })];
    const { unmount } = renderInbox([runningOn("Gateway", "Mac"), runningOn("Weekly", "")], runs);
    expect(screen.getByRole("button", { name: /^running · Mac › open the run of Gateway/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^running · default › open the run of Weekly/ })).toBeInTheDocument();
    unmount();
    renderInbox([runningOn("Gateway", "Mac")], [runs[0]]);
    expect(screen.getByRole("button", { name: /^running › open the run of Gateway/ })).toBeInTheDocument();
  });

  it("says paused on a running video's pill once its run holds at a pause, and pausing while its call ends defect", () => {
    // Defect: the owner paused a run on a second machine, it held, and its pill still said "running · <set> ›". Fix:
    // the pill says "paused · <set> ›" once the run holds, "pausing · <set> ›" while its model call still ends.
    const runningOn = (name: string, providerSet: string, pause: "" | "paused" | "pausing") => makeInboxItem(`${name}.mp4`,
      "running", { runs: [{ root: "output", name, status: "running", is_live: true, has_document: false, topic: "",
        display_name: "", stopped_phase: 0, provider_set: providerSet, pause }] });
    const runs = [makeRun({ root: "output", name: "Gateway", is_live: true, status: "running", provider_set: "gpu-box", pause: "paused" }),
      makeRun({ root: "output", name: "Weekly", is_live: true, status: "running", provider_set: "Office", pause: "pausing" }),
      makeRun({ root: "output", name: "Refunds", is_live: true, status: "running", provider_set: "Mac", pause: "" })];
    renderInbox([runningOn("Gateway", "gpu-box", "paused"), runningOn("Weekly", "Office", "pausing"), runningOn("Refunds", "Mac", "")], runs);
    expect(screen.getByRole("button", { name: /^paused · gpu-box › open the run of Gateway/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^pausing · Office › open the run of Weekly/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^running · Mac › open the run of Refunds/ })).toBeInTheDocument();
  });

  it("names the provider set on a done video's pill when its run was not made on the default set", async () => {
    // The owner: "If done was not on the default provider set, it should show it"; a done run on the default set keeps
    // its plain "done ›"
    const doneOn = (name: string, providerSet: string) => makeInboxItem(`${name}.mp4`, "done", { runs: [
      { root: "output", name, status: "complete", is_live: false, has_document: true, topic: "", display_name: "",
        stopped_phase: 0, provider_set: providerSet }] });
    renderInbox([doneOn("Gateway", "gpu-box"), doneOn("Weekly", ""), doneOn("Refunds", "default")], []);
    await openDone();
    expect(screen.getByRole("button", { name: /^done · gpu-box › open the run of Gateway/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^done › open the run of Weekly/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^done › open the run of Refunds/ })).toBeInTheDocument();
  });

  it("a blocked run queued again says queued in its row, as its own page does defect", () => {
    // Defect: a run blocked in phase 3 and queued again showed "blocked in phase 3" in the Inbox while its page said
    // "Queued as job ...": the row looked at a waiting job only for a run with a document. Fix: a waiting job wins.
    const folder = "/work/output/Blocked-call";
    const run = makeRun({ root: "output", name: "Blocked-call", folder, status: "incomplete", stopped_phase: 3, is_blocked: true });
    renderInbox([], [run], [makeJob({ job_id: "job-9" }, { video: "/work/output/Blocked-call/recording/x.mp4", output_root: folder })]);
    const row = screen.getByRole("button", { name: /Blocked-call/ });
    expect(row).toHaveTextContent("queued ›");
    expect(row).not.toHaveTextContent("blocked");
    expect(row.querySelector(".pill")).toHaveClass("queued");
  });

  it("a started run queued to go on says queued: continue, one queued to start over says queued defect", () => {
    // Defect: every waiting run said "queued", so the owner could not tell a run that goes on after a block or a pause
    // from one queued to start over. Fix: a resume, or a start at a later phase, says "queued: continue".
    const runOf = (name: string) => makeRun({ root: "output", name, folder: `/work/output/${name}`, status: "incomplete",
      stopped_phase: 3 });
    const jobOf = (id: string, name: string, request: Partial<JobRequest>) => makeJob({ job_id: id },
      { video: `/work/output/${name}/recording/x.mp4`, output_root: `/work/output/${name}`, ...request });
    renderInbox([], [runOf("Resumed"), runOf("From-four"), runOf("Again")],
      [jobOf("job-1", "Resumed", { is_resuming: true }), jobOf("job-2", "From-four", { from_phase: 4 }),
        jobOf("job-3", "Again", { is_resuming: false, from_phase: null })]);
    expect(screen.getByRole("button", { name: /Resumed/ })).toHaveTextContent("queued: continue ›");
    expect(screen.getByRole("button", { name: /From-four/ })).toHaveTextContent("queued: continue ›");
    const again = screen.getByRole("button", { name: /Again/ });
    expect(again).toHaveTextContent("queued ›");
    expect(again).not.toHaveTextContent("continue");
  });

  it("opens the live run of a video from its pill, else its first run", async () => {
    const running = makeInboxItem("Gateway.mp4", "running", { runs: [
      { root: "output", name: "Gateway-old", status: "incomplete", is_live: false, has_document: false, topic: "", display_name: "", stopped_phase: 0 },
      { root: "knowledge", name: "Gateway", status: "running", is_live: true, has_document: false, topic: "", display_name: "", stopped_phase: 0 },
    ] });
    const { onOpenRun } = renderInbox([running, DONE_ITEM]);
    expect(screen.getByRole("button", { name: /^running ›/ })).toHaveClass("running");
    await userEvent.click(screen.getByRole("button", { name: /^running ›/ }));
    expect(onOpenRun).toHaveBeenLastCalledWith("knowledge", "Gateway");
    await openDone();
    await userEvent.click(screen.getByRole("button", { name: /^done ›/ }));
    expect(onOpenRun).toHaveBeenLastCalledWith("knowledge", "AcmeSync-demo");
  });

  it("renames a new video only, keeps its extension, and shows a refusal in the dialog", async () => {
    let answer: unknown = answerJson({ detail: "AcmeSync.mp4 is already in the inbox" }, 400);
    const calls = installServer({ "GET /api/inbox/trash": [], "POST /api/inbox/rename": () => answer });
    const { onMessage, onChanged } = renderInbox();
    expect(within(screen.getByText("Jonas-call.mp4").closest("label")!).queryByRole("button", { name: /^rename/ })).toBeNull();
    await userEvent.click(within(screen.getByText("Gateway.mp4").closest("label")!).getByRole("button", { name: "rename Gateway.mp4" }));
    const dialog = screen.getByRole("dialog");
    const input = within(dialog).getByRole("textbox", { name: "new name" });
    expect(input).toHaveValue("Gateway.mp4");
    await userEvent.clear(input);
    await userEvent.type(input, "AcmeSync");
    await userEvent.click(within(dialog).getByRole("button", { name: "Rename" }));
    expect(await within(dialog).findByText("400: AcmeSync.mp4 is already in the inbox")).toBeInTheDocument();
    answer = { video: "/work/inbox/Gateway kickoff.mp4", name: "Gateway kickoff.mp4" };
    await userEvent.clear(input);
    expect(within(dialog).getByRole("button", { name: "Rename" })).toBeDisabled();
    await userEvent.type(input, "Gateway kickoff{Enter}");
    expect(calls.filter((call) => call.url === "/api/inbox/rename").at(-1)?.body)
      .toEqual({ video: "/work/inbox/Gateway.mp4", name: "Gateway kickoff" });
    expect(onMessage).toHaveBeenCalledWith("Renamed Gateway.mp4 to Gateway kickoff.mp4");
    expect(onChanged).toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("starts a rename from the whole name, so a dotted meeting folder keeps its last part", async () => {
    installServer({ "GET /api/inbox/trash": [] });
    renderInbox([makeInboxItem("Sprint review 2.5", "new", { parts: [{ name: "a.mp4" }, { name: "b.mp4" }] as InboxItem["parts"] })]);
    await userEvent.click(screen.getByRole("button", { name: "rename Sprint review 2.5" }));
    expect(screen.getByRole("textbox", { name: "new name" })).toHaveValue("Sprint review 2.5");
  });

  it("shows the topic phase 4 wrote under a video's name and under a run row's name", () => {
    const topical = makeInboxItem("Gateway.mp4", "started", { topic: "Gateway token exchange" });
    renderInbox([topical, NEW_ITEM], [makeRun({ root: "output", name: "Jonas-old", status: "incomplete", topic: "Jonas onboarding" })]);
    expect(screen.getByText("Gateway token exchange")).toHaveClass("topic");
    expect(screen.getByText("Jonas onboarding")).toHaveAttribute("title", "Jonas onboarding");
    expect(document.querySelectorAll(".topic")).toHaveLength(2);
  });

  it("shows a meeting in parts as one row with its part count", () => {
    const meeting = makeInboxItem("Weekly", "new", { video: "/work/inbox/Weekly", size_bytes: 3_000_000,
      parts: [{ name: "part 1.mp4", size_bytes: 1_000_000 }, { name: "part 2.mp4", size_bytes: 2_000_000 }] });
    renderInbox([meeting]);
    expect(screen.getByText("3 MB · 2 parts")).toHaveAttribute("title", "part 1.mp4\npart 2.mp4");
  });

  it("a run folder's row names no root folder defect", () => {
    // Every run row carried "output", the folder all runs are in, which told nothing; the row now shows its file type
    // alone
    renderInbox([], [makeRun({ root: "output", name: "Acme-done", status: "incomplete", file_type: "video" })]);
    expect(screen.getByRole("button", { name: /Acme-done/ })).not.toHaveTextContent(/output/);
  });

  it("a done run folder's row shows its recording's size defect", () => {
    // A done run's row showed only its file type, since its recording had moved into the run folder; the row now shows
    // the size the run list gives for the recording kept there, and none when it keeps none
    renderInbox([], [makeRun({ root: "output", name: "Acme-done", status: "incomplete", file_type: "pdf", size_bytes: 27_803_017 }),
      makeRun({ root: "output", name: "Gateway-done", status: "incomplete", file_type: "pdf", size_bytes: 0 })]);
    expect(screen.getByRole("button", { name: /Acme-done/ }).querySelector(".row-main small")).toHaveTextContent(/^pdf28 MB$/);
    expect(screen.getByRole("button", { name: /Gateway-done/ }).querySelector(".row-main small")).toHaveTextContent(/^pdf$/);
  });

  it("shows each video's and each run folder's own file type in a pill: video, audio or pdf", () => {
    const lesson = makeInboxItem("Jonas-lesson.mp3", "new", { file_type: "audio" });
    const report = makeInboxItem("Gateway-report.pdf", "new", { file_type: "pdf" });
    renderInbox([lesson, report, makeInboxItem("Acme.mp4", "new", { file_type: "video" })],
      [makeRun({ root: "output", name: "Jonas-old", status: "incomplete", file_type: "pdf" })]);
    expect([...document.querySelectorAll(".pill.file-type")].map((pill) => pill.textContent).sort())
      .toEqual(["audio", "pdf", "pdf", "video"]);
    expect(screen.getByRole("button", { name: /Jonas-old/ }).querySelector(".row-main small")).toHaveTextContent(/^pdf$/);
  });

  it("shows only the rows of the type chosen under the search, and all of them again", async () => {
    renderInbox([makeInboxItem("Jonas-lesson.mp3", "new", { file_type: "audio" }),
      makeInboxItem("Acme.mp4", "new", { file_type: "video" })],
      [makeRun({ root: "output", name: "Gateway-report", status: "incomplete", file_type: "pdf" })]);
    await userEvent.click(screen.getByRole("button", { name: "search and filter" }));
    await userEvent.click(screen.getByRole("button", { name: "pdf" }));
    expect(screen.getByRole("button", { name: "pdf" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText("Jonas-lesson.mp3")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Gateway-report/ })).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("find a video or a run"), "Acme");
    expect(screen.getByText('Nothing in the inbox is a pdf and matches "Acme".')).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "all" }));
    expect(screen.getByText("Acme.mp4")).toBeInTheDocument();
  });

  it("starts a run from a new or started video's ▶, and a done video has none", async () => {
    const calls = installServer({ "GET /api/inbox/trash": [], "POST /api/jobs": [makeJob()] });
    renderInbox();
    await userEvent.click(within(screen.getByText("Gateway.mp4").closest("label")!).getByRole("button", { name: "start a run of Gateway.mp4" }));
    expect(calls.find((call) => call.method === "POST" && call.url === "/api/jobs")?.body)
      .toMatchObject({ items: [{ video: "/work/inbox/Gateway.mp4" }] });
    expect(within(screen.getByText("Jonas-call.mp4").closest("label")!).getByRole("button", { name: "resume the run of Jonas-call.mp4" }))
      .toHaveAttribute("title", expect.stringContaining("resume"));
    await userEvent.click(screen.getByRole("button", { name: /^Done/ }));
    expect(within(screen.getByText("AcmeSync-demo.mp4").closest("label")!).queryByRole("button", { name: /start a run|resume the run/ })).toBeNull();
  });

  it("shows a video under its run's AI name, and its topic only when it says something else", () => {
    renderInbox([
      makeInboxItem("Recording-0101.mp4", "started", { topic: "Gateway refund rules", display_name: "Gateway refund rules" }),
      makeInboxItem("Recording-0102.mp4", "new", { topic: "Jonas onboarding" }),
    ]);
    expect(screen.queryByText("Recording-0101.mp4")).not.toBeInTheDocument();
    expect(screen.getAllByText("Gateway refund rules")).toHaveLength(1);
    expect(screen.getByText("Recording-0102.mp4")).toBeInTheDocument();
    expect(screen.getByText("Jonas onboarding")).toHaveClass("topic");
  });

  it("names the phase a started video's run and a started run folder stopped in, on the pill and in its title", async () => {
    const stopped = makeInboxItem("Jonas-call.mp4", "started", { runs: [{ root: "knowledge", name: "Jonas-call", status: "incomplete",
      is_live: false, has_document: false, topic: "", display_name: "", stopped_phase: 3 }] });
    const loose = makeRun({ root: "output", name: "Acme-half", video: "", status: "incomplete", stopped_phase: 2 });
    renderInbox([stopped], [loose]);
    const pill = screen.getByRole("button", { name: /^phase 3 ›/ });
    expect(pill).toHaveClass("incomplete");
    expect(pill.getAttribute("title")).toMatch(/^stopped in phase 3, frames processing\nopen its run: knowledge\/Jonas-call/);
    expect(screen.getByText("phase 2 ›")).toHaveAttribute("title", "stopped in phase 2, frames preparing");
  });

  it("names a Fast run's stopped phase 3 by the title the run gives it, and an ordinary run's by the general one defect", () => {
    const fast = makeInboxItem("Jonas-call.mp4", "started", { runs: [{ root: "knowledge", name: "Jonas-call", status: "incomplete",
      is_live: false, has_document: false, topic: "", display_name: "", stopped_phase: 3,
      phase_titles: { "3": "processing and draft writing", "4": "document reshaping" } }] });
    const ordinary = makeRun({ root: "output", name: "Acme-half", video: "", status: "incomplete", stopped_phase: 3 });
    renderInbox([fast], [ordinary]);
    const pills = screen.getAllByText("phase 3 ›");
    expect(pills.map((pill) => pill.getAttribute("title")?.split("\n")[0])).toEqual([
      "stopped in phase 3, processing and draft writing", "stopped in phase 3, frames processing"]);
  });

  it("says blocked on a blocked run's pill, and lists a branch of a video apart from the video's own run", async () => {
    const blocked = makeInboxItem("Jonas-call.mp4", "started", { runs: [{ root: "knowledge", name: "Jonas-call", status: "blocked",
      is_live: false, has_document: false, topic: "", display_name: "", stopped_phase: 3, is_blocked: true }] });
    const done = makeInboxItem("Gateway.mp4", "done");
    // a branch of Gateway names Gateway's video, yet it is a run of its own: it stands under Not finished
    const branch = makeRun({ root: "knowledge", name: "Gateway-from-3", video: done.video, status: "blocked", stopped_phase: 3,
      is_blocked: true, is_derived: true });
    renderInbox([blocked, done], [makeRun({ root: "knowledge", name: "Gateway", video: done.video, status: "complete", has_document: true }), branch]);
    const pill = screen.getByRole("button", { name: /^blocked in phase 3 › open the run of Jonas-call/ });
    expect(pill).toHaveClass("failed");
    expect(screen.getByText("Gateway-from-3")).toBeInTheDocument();
    expect(screen.getAllByText("blocked in phase 3 ›")).toHaveLength(2);
  });

  it("finds a video or a run by name, AI name or topic in any section, opening the sections that hold a match", async () => {
    const daily = makeInboxItem("Daily-standup.mp4", "done", { topic: "Gateway rollout" });
    const loose = makeRun({ root: "output", name: "Weekly-daily-sync", video: "", status: "complete", has_document: true });
    renderInbox([NEW_ITEM, STARTED_ITEM, daily], [loose]);
    await userEvent.click(screen.getByRole("button", { name: "search and filter" }));
    // Done is closed: the search opens it for its matches and leaves out every row that does not match
    await userEvent.type(screen.getByRole("searchbox", { name: "find a video or a run" }), "daily");
    expect(screen.getByText("Daily-standup.mp4")).toBeInTheDocument();
    expect(screen.getByText("Weekly-daily-sync")).toBeInTheDocument();
    expect(screen.queryByText("Gateway.mp4")).not.toBeInTheDocument();
    await userEvent.clear(screen.getByRole("searchbox", { name: "find a video or a run" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "find a video or a run" }), "rollout");
    expect(screen.getByText("Daily-standup.mp4")).toBeInTheDocument();
    await userEvent.type(screen.getByRole("searchbox", { name: "find a video or a run" }), "zzz");
    expect(screen.getByText(/Nothing in the inbox matches/)).toBeInTheDocument();
  });

  it("gives a running video no ▶", () => {
    renderInbox([makeInboxItem("Gateway.mp4", "running")]);
    expect(within(screen.getByText("Gateway.mp4").closest("label")!).queryByRole("button", { name: /start a run|resume the run/ })).toBeNull();
  });
});

describe("InboxPanel processing", () => {
  it("names its action with nothing ticked and queues every new video defect", async () => {
    // Defect: with nothing ticked the run button read "Select videos", an instruction, greyed out, beside an unlabelled arrow.
    // Fix: it reads and does "Process all new (N)" then, the ticked videos once any are ticked; its ⚙ arrow holds the run
    // settings that a separate "Phases" button held, so the bar has one action.
    const calls = installServer({ "GET /api/inbox/trash": [], "POST /api/jobs": [makeJob()] });
    renderInbox();
    expect(screen.queryByRole("button", { name: /Select videos/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Phases/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "run settings" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Process all new \(1\)/ }));
    expect((calls.at(-1)?.body as { items: { video: string }[] }).items.map((item) => item.video))
      .toEqual([NEW_ITEM.video]);
  });

  it("opens the run settings as a dialog with a tab for each part defect", async () => {
    // Defect: the ⚙ arrow opened a narrow menu folding phases, settings, models and tags into one another, and then a
    // dialog of cards side by side, a ragged wall of widgets. Fix: a dialog with a tab for each part, its choice under
    // its title, and the Process button at its foot
    installServer({ "GET /api/inbox/trash": [], "POST /api/jobs": [makeJob()] });
    renderInbox();
    await userEvent.click(screen.getByRole("button", { name: "run settings" }));
    const dialog = screen.getByRole("dialog", { name: /Run settings · every new recording \(1\)/ });
    // at the page's top level: inside the inbox panel, which a phone hides behind a run's page, it opened unseen
    expect(dialog.parentElement).toBe(document.body);
    expect(within(dialog).getByRole("tabpanel", { name: "phases" })).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("tab", { name: /^Settings/ }));
    expect(within(dialog).getByRole("tabpanel", { name: "settings" })).toHaveTextContent("Dry run");
    expect(within(dialog).queryByRole("group")).not.toBeInTheDocument();
    expect(dialog.querySelector("details")).toBeNull();
    expect(within(dialog).getByRole("button", { name: /Process all new \(1\)/ })).toBeInTheDocument();
  });

  it("names ticked PDFs, offers their mode and scans, and sends those with the PDFs alone defect", async () => {
    // Defect: a ticked PDF read "Process 1 video", the ⚙ arrow listed the video's phases, and a PDF's mode and scans
    // could be chosen only on its own page. Fix: the button names PDFs, the phases are the PDF's, and the choices go with them.
    const calls = installServer({ "GET /api/inbox/trash": [], "POST /api/jobs": [makeJob()],
      "GET /api/run-defaults?profile=": { pdf_mode: "text-only", pdf_ocr_engine: "tesseract",
        knowledge_filter_model: "google-SigLIP-2", workflow: "quality" },
      "GET /api/phases?medium=pdf-text-only": [{ ...PHASES[0], title: "text reading" }] });
    renderInbox([makeInboxItem("Handbook.pdf", "new"), makeInboxItem("Atlas.pdf", "new"), NEW_ITEM]);
    await tick("Handbook.pdf");
    await tick("Atlas.pdf");
    expect(screen.getByRole("button", { name: /Process 2 PDFs/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "run settings" }));
    // the run settings open as a dialog, the ticked PDFs' choices one tab of it
    const dialog = screen.getByRole("dialog", { name: /Run settings · 2 PDFs ticked/ });
    expect(await screen.findByText(/text reading/)).toBeInTheDocument();
    const pdfTab = within(dialog).getByRole("tab", { name: /^PDF/ });
    expect(pdfTab).toHaveTextContent("default processing, default document, default scans");
    await userEvent.click(pdfTab);
    // drop-down lists showing the default's value with a "default" pill beside each (the owner)
    const processingList = within(dialog).getByRole("combobox", { name: "Processing" }) as HTMLSelectElement;
    await screen.findByRole("option", { name: "default: Text" });
    expect(processingList.value).toBe("");
    expect(within(dialog).getAllByText("default")).toHaveLength(3);
    await userEvent.selectOptions(processingList, "pages");
    await userEvent.selectOptions(within(dialog).getByRole("combobox", { name: "Scans" }), "Unlimited-OCR-2");
    // the document shaping is still the default one
    expect(within(dialog).queryAllByText("default")).toHaveLength(1);
    await userEvent.click(within(dialog).getByRole("button", { name: "Done" }));
    await tick("Gateway.mp4");
    expect(screen.getByRole("button", { name: /Process 3 recordings/ })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Process 3 recordings/ }));
    const bodies = calls.filter((call) => call.url === "/api/jobs").map((call) => call.body as Record<string, unknown>);
    const pdfBody = bodies.find((body) => (body.items as { video: string }[])[0].video.endsWith(".pdf"))!;
    const videoBody = bodies.find((body) => (body.items as { video: string }[])[0].video.endsWith(".mp4"))!;
    expect(pdfBody).toMatchObject({ pdf_mode: "pages-as-images", pdf_ocr_engine: "unlimited-ocr", ingesting_profile: "" });
    expect(pdfBody).not.toHaveProperty("pdf_reader");
    expect((pdfBody.items as unknown[]).length).toBe(2);
    expect(videoBody).not.toHaveProperty("pdf_mode");
    expect(videoBody).not.toHaveProperty("pdf_ocr_engine");
  });

  it("names a ticked PDF run folder, offers its PDF tab and queues it in its own folder defect", async () => {
    // Defect: a ticked PDF run whose recording the inbox no longer listed showed "every new recording (36)", no PDF tab
    // and a disabled "No video ticked". Fix: a ticked run folder is a row Process queues in its folder, as its Resume does
    const calls = installServer({ "GET /api/inbox/trash": [], "POST /api/jobs": [makeJob()],
      "GET /api/phases?medium=pdf-text-only": [{ ...PHASES[0], title: "text reading" }] });
    const pdfRun = makeRun({ root: "output", name: "Handbook", folder: "/work/output/Handbook",
      video: "/work/inbox/Handbook.pdf", file_type: "pdf", status: "incomplete", stopped_phase: 4 });
    renderInbox([NEW_ITEM], [pdfRun]);
    await userEvent.click(screen.getByRole("checkbox", { name: "tick Handbook" }));
    expect(screen.getByRole("button", { name: /Process 1 PDF$/ })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "run settings" }));
    const dialog = screen.getByRole("dialog", { name: /Run settings · 1 PDF ticked/ });
    expect(within(dialog).getByRole("tab", { name: /^PDF/ })).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: /Process 1 PDF/ }));
    const body = calls.find((call) => call.url === "/api/jobs")?.body as { items: unknown[]; is_resuming: boolean };
    expect(body.items).toEqual([{ video: "/work/inbox/Handbook.pdf", output: "/work/output/Handbook" }]);
    expect(body.is_resuming).toBe(true);
    expect(screen.getByRole("checkbox", { name: "tick Handbook" })).not.toBeChecked();
  });

  it("queues the ticked videos with the chosen phases and says so", async () => {
    const calls = installServer({ "GET /api/inbox/trash": [], "POST /api/jobs": [makeJob()] });
    const { onMessage, onChanged } = renderInbox();
    expect(screen.getByRole("button", { name: /Process all new \(1\)/ })).toBeEnabled();
    await tick("Gateway.mp4");
    await userEvent.click(screen.getByRole("button", { name: "run settings" }));
    await userEvent.click(screen.getByRole("radio", { name: "Only" }));
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    // a choice other than the default shows on the Process button itself
    expect(screen.getByRole("button", { name: /Process 1 video/ })).toHaveTextContent("· phase 1");
    await userEvent.click(screen.getByRole("button", { name: /Process 1 video/ }));
    expect(calls.at(-1)).toEqual({ method: "POST", url: "/api/jobs", body: {
      items: [{ video: "/work/inbox/Gateway.mp4", output: "/work/knowledge/Gateway" }], only_phase: 1, from_phase: null,
      to_phase: null, worker_indexes: [], is_resuming: false, is_dry_run: false, profile: "", tags: [],
      ingesting_profile: "", is_run_as_audio: false } });
    // Nothing else runs, so the job starts at once: told "queued" with no run going, the user did not know to wait
    expect(onMessage).toHaveBeenCalledWith("Starting Gateway.mp4: it shows as starting until its run appears");
    expect(onChanged).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /Process all new/ })).toBeInTheDocument();
  });

  it("resumes a started video's run when every phase is chosen, and starts a new video's", async () => {
    const calls = installServer({ "GET /api/inbox/trash": [], "POST /api/jobs": [makeJob()] });
    renderInbox();
    await userEvent.click(screen.getByText("Jonas-call.mp4").closest("label")!.querySelector<HTMLElement>(".play-button")!);
    const jobRequests = () => calls.filter((call) => call.url === "/api/jobs").map((call) => call.body);
    expect(jobRequests().at(-1)).toMatchObject({ items: [{ video: STARTED_ITEM.video }], is_resuming: true });
    await tick("Gateway.mp4");
    await tick("Jonas-call.mp4");
    await userEvent.click(screen.getByRole("button", { name: /Process 2/ }));
    expect(jobRequests().slice(-2)).toEqual([
      expect.objectContaining({ items: [expect.objectContaining({ video: "/work/inbox/Gateway.mp4" })], is_resuming: false }),
      expect.objectContaining({ items: [expect.objectContaining({ video: STARTED_ITEM.video })], is_resuming: true }),
    ]);
  });

  it("resumes a started video's run with the settings profile the run was made with, while that profile exists", async () => {
    const calls = installServer({ "GET /api/inbox/trash": [], "POST /api/jobs": [makeJob()], "GET /api/profiles": { profiles: ["strict"] } });
    renderInbox([STARTED_ITEM], [makeRun({ name: "Jonas-call", folder: "/work/knowledge/Jonas-call", settings_profile: "strict" })]);
    await screen.findByText("Jonas-call.mp4");
    await userEvent.click(screen.getByText("Jonas-call.mp4").closest("label")!.querySelector<HTMLElement>(".play-button")!);
    expect(calls.filter((call) => call.url === "/api/jobs").at(-1)?.body).toMatchObject({ is_resuming: true, profile: "strict" });
  });

  it("starts a video with the tags given on its own page, beside the ones chosen in the Phases menu", async () => {
    const calls = installServer({ "GET /api/inbox/trash": [], "POST /api/jobs": [makeJob()] });
    renderInbox([makeInboxItem("Gateway.mp4", "new", { tags: ["client Acme"] })]);
    await userEvent.click(screen.getByText("Gateway.mp4").closest("label")!.querySelector<HTMLElement>(".play-button")!);
    expect(calls.filter((call) => call.url === "/api/jobs").at(-1)?.body).toMatchObject({ tags: ["client Acme"] });
  });

  it("says a video waits behind the running job", async () => {
    installServer({ "GET /api/inbox/trash": [], "POST /api/jobs": [makeJob()] });
    const { onMessage } = renderInbox(undefined, [], [makeJob({ status: "running" }, { video: "/work/inbox/Jonas-call.mp4" })]);
    await tick("Gateway.mp4");
    await userEvent.click(screen.getByRole("button", { name: /Process 1/ }));
    expect(onMessage).toHaveBeenCalledWith("Queued Gateway.mp4 behind 1 job");
  });

  it("shows a video with a waiting or starting job as such, never as new, and its box cannot be ticked again", () => {
    const waiting = makeJob({ job_id: "job-2", status: "queued" }, { video: "/work/inbox/Gateway.mp4" });
    renderInbox(undefined, [], [waiting]);
    const row = screen.getByText("Gateway.mp4").closest("label")!;
    expect(within(row).getByRole("button", { name: /queued/ })).toBeInTheDocument();
    expect(within(row).queryByText("new")).not.toBeInTheDocument();
    expect(row.querySelector("input")).toBeDisabled();
  });

  it("shows a started video sent to the queue again as queued, opening its run's page, and keeps its run pill otherwise defect", async () => {
    // Defect: the queued pill opened the job's console alone, with neither the video nor what the run produced (the
    // owner). Fix: it opens the run's page, which links to the console.
    const waiting = makeJob({ job_id: "job-5", status: "queued" }, { video: STARTED_ITEM.video });
    const { onOpenJob, onOpenRun, unmount } = renderInbox(undefined, [], [waiting]);
    const row = screen.getByText(STARTED_ITEM.name).closest("label")!;
    expect(within(row).queryByRole("button", { name: /not finished/ })).not.toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: /queued/ }));
    expect(onOpenRun).toHaveBeenCalled();
    expect(onOpenJob).not.toHaveBeenCalled();
    expect(row.querySelector("input")).toBeDisabled();
    unmount();

    renderInbox(undefined, [], [makeJob({ status: "finished", exit_code: 0 }, { video: STARTED_ITEM.video })]);
    expect(within(screen.getByText(STARTED_ITEM.name).closest("label")!).getByRole("button", { name: /not finished/ }))
      .toBeInTheDocument();
  });

  it("shows a video whose job runs before its run folder appears as starting", () => {
    renderInbox(undefined, [], [makeJob({ status: "running" }, { video: "/work/inbox/Gateway.mp4" })]);
    expect(within(screen.getByText("Gateway.mp4").closest("label")!).getByRole("button", { name: /starting…/ })).toBeInTheDocument();
  });

  it("a started video's row queued to go on says queued: continue, one queued to start over says queued defect", () => {
    // Defect: on the real page a resume of a video in the Review later list still said "queued": its row used the new
    // video's pill, which never looked at whether the job goes on. Fix: that pill says "queued: continue" too.
    for (const [request, label] of [[{ is_resuming: true }, "queued: continue ›"], [{ from_phase: 4 }, "queued: continue ›"],
      [{ is_resuming: false, from_phase: null }, "queued ›"]] as const) {
      const { unmount } = renderInbox(undefined, [], [makeJob({ status: "queued" }, { video: STARTED_ITEM.video, ...request })]);
      const row = screen.getByText(STARTED_ITEM.name).closest("label")!;
      expect(within(row).getByRole("button", { name: /queued/ })).toHaveTextContent(new RegExp(`^${label}$`));
      unmount();
    }
  });

  it("shows a running job whose run folder is gone as such, never as starting, and opens its console", async () => {
    const lost = makeJob({ job_id: "job-4", status: "running", is_output_missing: true }, { video: "/work/inbox/Gateway.mp4" });
    const { onOpenJob } = renderInbox(undefined, [], [lost]);
    const row = screen.getByText("Gateway.mp4").closest("label")!;
    expect(within(row).queryByRole("button", { name: /starting…/ })).not.toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: /run folder missing/ }));
    expect(onOpenJob).toHaveBeenCalledWith(lost);
  });

  it("opens a new video's own page from its queued or starting pill, never the job's console alone defect", async () => {
    // Defect: a queued new video opened as its job's console, showing neither the video nor anything it produced (the
    // owner). Fix: the pill opens the video's page, which plays it and links to the console.
    for (const status of ["queued", "running"] as const) {
      const job = makeJob({ status }, { video: "/work/inbox/Gateway.mp4" });
      const { onOpenJob, onOpenVideo, unmount } = renderInbox(undefined, [], [job]);
      await userEvent.click(within(screen.getByText("Gateway.mp4").closest("label")!)
        .getByRole("button", { name: status === "queued" ? /queued/ : /starting…/ }));
      expect(onOpenVideo).toHaveBeenCalledWith(expect.objectContaining({ name: "Gateway.mp4" }));
      expect(onOpenJob).not.toHaveBeenCalled();
      unmount();
    }
  });

  it("leaves a video whose job already ended as the inbox says", () => {
    renderInbox(undefined, [], [makeJob({ status: "finished" }, { video: "/work/inbox/Gateway.mp4" })]);
    const row = screen.getByText("Gateway.mp4").closest("label")!;
    expect(within(row).getByRole("button", { name: /^new ›/ })).toBeInTheDocument();
    expect(row.querySelector("input")).not.toBeDisabled();
  });

  it("opens a new video's page from its name or its new pill, and a started video's run from its name", async () => {
    const { onOpenVideo, onOpenRun } = renderInbox();
    await userEvent.click(screen.getByText("Gateway.mp4"));
    expect(onOpenVideo).toHaveBeenCalledWith(expect.objectContaining({ name: "Gateway.mp4" }));
    await userEvent.click(screen.getByRole("button", { name: /^new ›/ }));
    expect(onOpenVideo).toHaveBeenCalledTimes(2);
    expect(onOpenRun).not.toHaveBeenCalled();
    await userEvent.click(screen.getByText("Jonas-call.mp4"));
    expect(onOpenRun).toHaveBeenCalledWith("knowledge", "Jonas-call");
    expect(onOpenVideo).toHaveBeenCalledTimes(2);
  });

  it("each list ticks all its videos at once, shows a dash when only some are ticked, and unticks them again", async () => {
    renderInbox([NEW_ITEM, makeInboxItem("Jonas.mp4", "new"), STARTED_ITEM, DONE_ITEM]);
    const fresh = screen.getByRole("checkbox", { name: "select every video in New" });
    const unfinished = screen.getByRole("checkbox", { name: "select every video in Not finished" });
    const done = screen.getByRole("checkbox", { name: "select every video in Done" });
    await userEvent.click(fresh);
    expect(screen.getByRole("button", { name: /Process 2/ })).toBeEnabled();
    expect(screen.getByText("2 ticked")).toBeInTheDocument();
    await userEvent.click(unfinished);
    await userEvent.click(done);
    expect(screen.getByRole("button", { name: /Process 4 videos/ })).toBeEnabled();
    await tick("Gateway.mp4");
    expect(fresh).toHaveProperty("indeterminate", true);
    expect(fresh).not.toBeChecked();
    await userEvent.click(fresh);
    expect(fresh).toHaveProperty("indeterminate", false);
    expect(screen.getByRole("button", { name: /Process 4/ })).toBeEnabled();
    await userEvent.click(fresh);
    await userEvent.click(unfinished);
    await userEvent.click(done);
    expect(screen.getByRole("button", { name: /Process all new/ })).toBeEnabled();
  });

  it("never queues a video again from the menu while its job waits or starts", async () => {
    const calls = installServer({ "GET /api/inbox/trash": [], "POST /api/jobs": [makeJob()] });
    const { onMessage } = renderInbox(undefined, [], [makeJob({ status: "queued" }, { video: NEW_ITEM.video })]);
    expect(screen.getByRole("button", { name: /Nothing new to process/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "run settings" }));
    await userEvent.click(screen.getByRole("button", { name: /Process every video/ }));
    const queued = calls.filter((call) => call.url === "/api/jobs").flatMap((call) => (call.body as { items: { video: string }[] }).items);
    expect(queued.map((item) => item.video)).not.toContain(NEW_ITEM.video);
    expect(onMessage).not.toHaveBeenCalledWith(expect.stringContaining("Gateway.mp4"));
  });

  it("names what was queued and what was refused when a later request fails, and unticks the queued videos", async () => {
    let count = 0;
    installServer({ "GET /api/inbox/trash": [],
      "POST /api/jobs": () => (count++ === 0 ? [makeJob()] : answerJson({ detail: "profile gone" }, 400)) });
    const { onMessage, onChanged } = renderInbox();
    await tick("Gateway.mp4");
    await tick("Jonas-call.mp4");
    await userEvent.click(screen.getByRole("button", { name: /Process 2/ }));
    expect(onMessage).toHaveBeenLastCalledWith("Queued Gateway.mp4; Jonas-call.mp4 refused: 400: profile gone", true);
    expect(onChanged).toHaveBeenCalled();
    expect(screen.getByRole("checkbox", { name: "tick Gateway.mp4" })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: "tick Jonas-call.mp4" })).toBeChecked();
  });

  it("says only the first of several videos starts, and the others wait behind it", async () => {
    installServer({ "GET /api/inbox/trash": [], "POST /api/jobs": [makeJob(), makeJob()] });
    const other = makeInboxItem("Other.mp4", "new");
    const { onMessage } = renderInbox([NEW_ITEM, other]);
    await userEvent.click(screen.getByRole("checkbox", { name: "select every video in New" }));
    await userEvent.click(screen.getByRole("button", { name: /Process 2/ }));
    expect(onMessage).toHaveBeenCalledWith("Starting Gateway.mp4: it shows as starting until its run appears; Other.mp4 queued behind it");
  });

  it("shows the models under each workflow the recordings take, with only the phases that call a model defect", async () => {
    // The Models tab listed every phase with no word of which workflow it was for, phases a PDF never runs among them.
    // Now each workflow has its own section, titled by it, listing only its phases that call a model.
    installServer({ "GET /api/inbox/trash": [],
      "GET /api/phase-models": { models: [{ name: "frame", spec: "lmstudio:big-vl" }, { name: "small", spec: "lmstudio:small-vl" }],
        phases: [{ phase: "frames_preparing", number: 2, title: "frames preparing", default: "frame" },
          { phase: "finalize", number: 4, title: "document writing", default: "frame" }] },
      "GET /api/run-defaults?profile=": { pdf_mode: "text-only", pdf_ocr_engine: "tesseract",
        knowledge_filter_model: "google-SigLIP-2", workflow: "quality" },
      "GET /api/phases?medium=pdf-text-only": [{ ...PHASES[1], number: 2, title: "paragraphs and figures preparing", is_model_used: false },
        { ...PHASES[3], number: 4, title: "document writing", is_model_used: true }] });
    renderInbox([makeInboxItem("Handbook.pdf", "new")]);
    await userEvent.click(screen.getByRole("button", { name: "run settings" }));
    await userEvent.click(screen.getByRole("tab", { name: /^Models/ }));
    const section = await screen.findByRole("region", { name: "PDF workflow · text only" });
    // each workflow is folded at first, its title saying no model is chosen (the owner: "collapsed by default")
    const fold = section.querySelector("details")!;
    expect(fold.open).toBe(false);
    expect(within(section).getByText("default models")).toBeInTheDocument();
    await userEvent.click(within(section).getByText("PDF workflow · text only"));
    expect(fold.open).toBe(true);
    expect(within(section).getByRole("combobox", { name: "4 · document writing" })).toBeInTheDocument();
    await userEvent.selectOptions(within(section).getByRole("combobox", { name: "4 · document writing" }), "small");
    expect(within(section).getByText("1 model chosen")).toBeInTheDocument();
    expect(within(section).queryByRole("combobox", { name: /^2 ·/ })).not.toBeInTheDocument();
  });

  it("explains a choice and each of its options behind the ? beside its list defect", async () => {
    // A choice named only by its tool's name ("pymupdf4llm") left the owner guessing what it did.
    // Now the ? beside each list opens what the choice is and what each option does.
    installServer({ "GET /api/inbox/trash": [],
      "GET /api/run-defaults?profile=": { pdf_mode: "text-only", pdf_ocr_engine: "tesseract",
        knowledge_filter_model: "google-SigLIP-2", workflow: "quality" } });
    renderInbox([makeInboxItem("Handbook.pdf", "new")]);
    await tick("Handbook.pdf");
    await userEvent.click(screen.getByRole("button", { name: "run settings" }));
    await userEvent.click(screen.getByRole("tab", { name: /^PDF/ }));
    expect(screen.queryByRole("note", { name: "Scans explained" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "what Scans means" }));
    expect(screen.getByRole("note", { name: "Scans explained" })).toHaveTextContent("PyMuPDF");
    expect(screen.getByRole("note", { name: "Scans explained" })).not.toHaveTextContent("MinerU");
  });

  it("leaves out a running recording asked with the same settings, and queues it with other ones defect", async () => {
    // A recording already running, ticked with others and processed, was queued a second time, and its row read
    // "queued". Now it is left out and named while the settings are its run's; with other settings it is queued.
    const running = makeInboxItem("Atlas.pdf", "running");
    const calls = installServer({ "GET /api/inbox/trash": [], "POST /api/jobs": [makeJob()] });
    const { onMessage } = renderInbox([running, NEW_ITEM], [],
      [makeJob({ status: "running" }, { video: running.video, pdf_mode: "", profile: "" })]);
    await tick("Atlas.pdf");
    await tick("Gateway.mp4");
    await userEvent.click(screen.getByRole("button", { name: /Process 2 recordings/ }));
    const sent = () => calls.filter((call) => call.url === "/api/jobs")
      .flatMap((call) => (call.body as { items: { video: string }[] }).items.map((item) => item.video));
    expect(sent()).toEqual([NEW_ITEM.video]);
    expect(onMessage).toHaveBeenLastCalledWith(expect.stringContaining("Atlas.pdf left out: already running with the same settings"));
    // with another PDF processing it is another run, and goes in the queue; left out, it stayed ticked
    await userEvent.click(screen.getByRole("button", { name: "run settings" }));
    await userEvent.click(screen.getByRole("tab", { name: /^PDF/ }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Processing" }), "pages");
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    await userEvent.click(screen.getByRole("button", { name: /Process 1 PDF/ }));
    expect(sent()).toEqual([NEW_ITEM.video, running.video]);
  });

  it("asks before processing a done recording again, and can leave it out defect", async () => {
    // A ticked recording already done was queued with the others and run from its first phase in its own run folder,
    // its document written over, with no word. Now a warning asks first: process again, skip the done ones, or cancel.
    const calls = installServer({ "GET /api/inbox/trash": [], "POST /api/jobs": [makeJob()] });
    renderInbox();
    await tick("Gateway.mp4");
    await openDone();
    await tick("AcmeSync-demo.mp4");
    await userEvent.click(screen.getByRole("button", { name: /Process 2 videos/ }));
    const warning = screen.getByRole("dialog", { name: "Process done recordings again?" });
    expect(warning).toHaveTextContent("AcmeSync-demo.mp4");
    expect(calls.filter((call) => call.url === "/api/jobs")).toHaveLength(0);
    await userEvent.click(within(warning).getByRole("button", { name: "Skip the done ones" }));
    const sent = calls.filter((call) => call.url === "/api/jobs").map((call) => call.body as { items: { video: string }[] });
    expect(sent.flatMap((body) => body.items.map((item) => item.video))).toEqual([NEW_ITEM.video]);
  });

  it("offers Process all new and Process every video, and reports a refused queue", async () => {
    const calls = installServer({ "GET /api/inbox/trash": [], "POST /api/jobs": answerJson({ detail: "queue full" }, 409) });
    const { onMessage } = renderInbox();
    await userEvent.click(screen.getByRole("button", { name: /Process all new \(1\)/ }));
    expect((calls.at(-1)?.body as { items: unknown[] }).items).toHaveLength(1);
    expect(onMessage).toHaveBeenLastCalledWith("Gateway.mp4 refused: 409: queue full", true);
    await userEvent.click(screen.getByRole("button", { name: "run settings" }));
    await userEvent.click(screen.getByRole("button", { name: /Process every video/ }));
    // the done video is asked about first
    await userEvent.click(within(screen.getByRole("dialog", { name: "Process done recordings again?" }))
      .getByRole("button", { name: "Process again" }));
    // the new and the done video in one request; the started one would follow as a resume, but the queue refused
    expect((calls.at(-1)?.body as { items: unknown[] }).items).toHaveLength(2);
  });
});

describe("InboxPanel trash", () => {
  it("warns before trashing a video that is not processed yet, and Keep them keeps it", async () => {
    const calls = installServer({ "GET /api/inbox/trash": [] });
    renderInbox();
    await tick("Gateway.mp4");
    await userEvent.click(screen.getByRole("button", { name: "move to trash" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByRole("heading", { name: "Move to trash?" })).toBeInTheDocument();
    expect(within(dialog).getByText("This file is not processed yet. Are you sure?")).toBeInTheDocument();
    // the page's own words for a run's state, the same as its pill
    expect(within(dialog).getByText("new")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Keep them" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.filter((call) => call.method === "POST")).toHaveLength(0);
  });

  it("names each video's state in the warning and moves them on Move to trash", async () => {
    const running = makeInboxItem("Live.mp4", "running");
    const calls = installServer({
      "GET /api/inbox/trash": [],
      "POST /api/inbox/trash": [
        { video: "/work/inbox/Jonas-call.mp4", moved_as: "Jonas-call.mp4", error: "" },
        { video: "/work/inbox/AcmeSync-demo.mp4", moved_as: "AcmeSync-demo.mp4", error: "" },
        { video: "/work/inbox/Live.mp4", moved_as: "Live.mp4", error: "" },
      ],
    });
    const { onMessage, onChanged } = renderInbox([STARTED_ITEM, DONE_ITEM, running]);
    await userEvent.click(screen.getByRole("checkbox", { name: "select every video in Running" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "select every video in Not finished" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "select every video in Done" }));
    await userEvent.click(screen.getByRole("button", { name: "move to trash" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("These files are not processed yet. Are you sure?")).toBeInTheDocument();
    expect(within(dialog).getByText("not finished")).toBeInTheDocument();
    expect(within(dialog).getByText("done")).toBeInTheDocument();
    expect(within(dialog).getByText("running")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Move to trash" }));
    expect(calls.find((call) => call.url === "/api/inbox/trash" && call.method === "POST")?.body)
      .toEqual({ videos: ["/work/inbox/Jonas-call.mp4", "/work/inbox/AcmeSync-demo.mp4", "/work/inbox/Live.mp4"], run_folders: [] });
    expect(onMessage).toHaveBeenCalledWith("Moved 3 to inbox/.trash. Their runs stay listed in the inbox.", false);
    expect(onChanged).toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("trashes a done video without asking, and reports each refusal or a lost request", async () => {
    let answer: unknown = [{ video: "/work/inbox/AcmeSync-demo.mp4", moved_as: "", error: "AcmeSync-demo.mp4 is open elsewhere" }];
    installServer({ "GET /api/inbox/trash": [], "POST /api/inbox/trash": () => answer });
    const { onMessage } = renderInbox();
    await openDone();
    await tick("AcmeSync-demo.mp4");
    await userEvent.click(screen.getByRole("button", { name: "move to trash" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onMessage).toHaveBeenLastCalledWith("AcmeSync-demo.mp4 is open elsewhere", true);
    answer = answerJson({ detail: "server error" }, 500);
    await tick("AcmeSync-demo.mp4");
    await userEvent.click(screen.getByRole("button", { name: "move to trash" }));
    expect(onMessage).toHaveBeenLastCalledWith("500: server error", true);
  });

  it("lists the trash and restores an entry", async () => {
    let restoreAnswer: unknown = [{ name: "Old.mp4", restored_as: "Old-2.mp4", error: "" }];
    const calls = installServer({
      "GET /api/inbox/trash": [{ name: "Old.mp4", is_folder: false, size_bytes: 5_000_000 },
        { name: "Gateway-meeting", is_folder: true, size_bytes: 2_000_000_000 }],
      "POST /api/inbox/trash/restore": () => restoreAnswer,
    });
    const { onMessage, onChanged } = renderInbox();
    const head = await screen.findByRole("button", { name: /Trash 2/ });
    expect(head).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(head);
    expect(screen.getByText("meeting folder · 2.0 GB")).toBeInTheDocument();
    const row = screen.getByText("Old.mp4").closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: /Restore/ }));
    expect(calls.at(-2)).toEqual({ method: "POST", url: "/api/inbox/trash/restore", body: { names: ["Old.mp4"] } });
    expect(onMessage).toHaveBeenLastCalledWith("Restored Old-2.mp4 to the inbox", false);
    expect(onChanged).toHaveBeenCalled();
    restoreAnswer = [{ name: "Old.mp4", restored_as: "", error: "Old.mp4 is gone" }];
    await userEvent.click(within(row).getByRole("button", { name: /Restore/ }));
    expect(onMessage).toHaveBeenLastCalledWith("Old.mp4 is gone", true);
    restoreAnswer = answerJson({ detail: "locked" }, 423);
    await userEvent.click(within(row).getByRole("button", { name: /Restore/ }));
    expect(onMessage).toHaveBeenLastCalledWith("423: locked", true);
    await userEvent.click(head);
    expect(screen.queryByText("Old.mp4")).not.toBeInTheDocument();
  });

  it("says when the trash cannot be read, and shows no trash", async () => {
    installServer({ "GET /api/inbox/trash": answerJson({ detail: "no" }, 500) });
    const { onMessage } = renderInbox();
    await vi.waitFor(() => expect(onMessage).toHaveBeenCalledWith("The trash could not be read: 500: no", true));
    expect(screen.queryByRole("button", { name: /Trash/ })).not.toBeInTheDocument();
  });

  it("unticks a restored entry, so the same name trashed again is not already picked for Delete permanently", async () => {
    installServer({ "GET /api/inbox/trash": [{ name: "Old.mp4", is_folder: false, size_bytes: 5 }],
      "POST /api/inbox/trash/restore": [{ name: "Old.mp4", restored_as: "Old.mp4", error: "" }] });
    renderInbox();
    await userEvent.click(await screen.findByRole("button", { name: /Trash 1/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: "select Old.mp4" }));
    await userEvent.click(within(screen.getByText("Old.mp4").closest("li")!).getByRole("button", { name: /Restore/ }));
    await vi.waitFor(() => expect(screen.getByRole("checkbox", { name: "select Old.mp4" })).not.toBeChecked());
    expect(screen.getByRole("button", { name: /Delete permanently/ })).toBeDisabled();
  });
});

describe("InboxPanel trash", () => {
  it("deletes the ticked trash entries for good after one confirmation that names each", async () => {
    const calls = installServer({
      "GET /api/inbox/trash": [{ name: "AcmeSync.mp4", is_folder: false, size_bytes: 10, video: "", run_folder: "" },
        { name: "Jonas-run", is_folder: true, size_bytes: 20, video: "", run_folder: "/work/output/Jonas-run" },
        { name: "Gateway.mp4", is_folder: false, size_bytes: 30, video: "", run_folder: "" }],
      "POST /api/inbox/trash/delete": [{ name: "AcmeSync.mp4", error: "" }, { name: "Jonas-run", error: "" }],
    });
    const { onMessage } = renderInbox([]);
    await userEvent.click(await screen.findByRole("button", { name: /Trash/ }));
    const remove = screen.getByRole("button", { name: /Delete permanently/ });
    expect(remove).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "select AcmeSync.mp4" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "select Jonas-run" }));
    await userEvent.click(remove);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/These 2 item\(s\) are deleted for good/)).toBeInTheDocument();
    expect(within(dialog).getByText(/a whole run folder/)).toBeInTheDocument();
    expect(within(dialog).queryByText("Gateway.mp4")).not.toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: /Delete permanently/ }));
    const sent = calls.find((call) => call.url === "/api/inbox/trash/delete");
    expect(sent?.body).toEqual({ names: ["AcmeSync.mp4", "Jonas-run"] });
    expect(onMessage).toHaveBeenLastCalledWith("Deleted 2 for good from inbox/.trash", false);
  });

  it("Keep them closes the confirmation and deletes nothing; select all ticks every entry", async () => {
    const calls = installServer({ "GET /api/inbox/trash": [{ name: "AcmeSync.mp4", is_folder: false, size_bytes: 10, video: "", run_folder: "" }] });
    renderInbox([]);
    await userEvent.click(await screen.findByRole("button", { name: /Trash/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: "select everything in the trash" }));
    expect(screen.getByRole("checkbox", { name: "select AcmeSync.mp4" })).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: /Delete permanently/ }));
    await userEvent.click(screen.getByRole("button", { name: "Keep them" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.some((call) => call.url === "/api/inbox/trash/delete")).toBe(false);
  });
});

describe("InboxPanel upload", () => {
  const recording = new File(["x"], "AcmeSync.mp4", { type: "video/mp4" });

  it("uploads picked files with progress and says what each was saved as", async () => {
    const { onChanged, container } = renderInbox([]);
    await userEvent.upload(container.querySelector<HTMLInputElement>("input[type=file]")!, recording);
    const request = FakeXMLHttpRequest.latest();
    act(() => request.reportProgress(40, 100));
    expect(screen.getByText("40%")).toBeInTheDocument();
    await act(async () => request.respond(200, [{ name: "AcmeSync.mp4", saved_as: "AcmeSync-2.mp4", error: "" }]));
    expect(screen.getByText("saved as AcmeSync-2.mp4")).toHaveClass("muted");
    expect(screen.queryByText("40%")).not.toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "dismiss" }));
    expect(screen.queryByText("saved as AcmeSync-2.mp4")).not.toBeInTheDocument();
  });

  it("a finished upload fades out after 10 seconds and leaves, while a refusal stays until it is dismissed",
     async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const { container } = renderInbox([]);
      await userEvent.upload(container.querySelector<HTMLInputElement>("input[type=file]")!, recording);
      await act(async () => FakeXMLHttpRequest.latest().respond(200, [{ name: "AcmeSync.mp4", saved_as: "AcmeSync.mp4", error: "" }]));
      const row = () => screen.queryByText("saved as AcmeSync.mp4")?.closest("li");
      expect(row()).not.toHaveClass("fading");
      await act(async () => { vi.advanceTimersByTime(10000); });
      expect(row()).toHaveClass("fading");
      await act(async () => { vi.advanceTimersByTime(1500); });
      expect(screen.queryByText("saved as AcmeSync.mp4")).not.toBeInTheDocument();

      await userEvent.upload(container.querySelector<HTMLInputElement>("input[type=file]")!, recording);
      await act(async () => FakeXMLHttpRequest.latest().respond(200, [{ name: "AcmeSync.mp4", saved_as: "",
        error: "AcmeSync.mp4 is no video", kind: "refused" }]));
      await act(async () => { vi.advanceTimersByTime(30000); });
      expect(screen.getByText("Not taken (1)")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("a recording processed already is refused with a link to the done run", async () => {
    const { container, onOpenRun } = renderInbox([]);
    await userEvent.upload(container.querySelector<HTMLInputElement>("input[type=file]")!, recording);
    await act(async () => FakeXMLHttpRequest.latest().respond(200, [{ name: "AcmeSync.mp4", saved_as: "",
      error: "AcmeSync.mp4 is a recording processed already: output/AcmeSync-review is done", kind: "processed",
      same_as: "output/AcmeSync-review", duplicate_of: { root: "output", name: "AcmeSync-review" } }]));
    expect(screen.getByText("Skipped: processed already (1)")).toHaveClass("error");
    expect(screen.getByText("as output/AcmeSync-review")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Open output/AcmeSync-review" }));
    expect(onOpenRun).toHaveBeenCalledWith("output", "AcmeSync-review");
  });

  it("a dropped zip of processed recordings only saves nothing and names each run", async () => {
    const { container } = renderInbox([]);
    fireEvent.drop(container.querySelector(".dropzone")!, { dataTransfer: { files: [new File(["x"], "pack.zip")] } });
    await act(async () => {});
    await act(async () => FakeXMLHttpRequest.latest().respond(200, { added: [], refused: [
      { name: "Jonas.mp4", saved_as: "", error: "Jonas.mp4 is a recording processed already: output/Jonas is done",
        kind: "processed", same_as: "output/Jonas", duplicate_of: { root: "output", name: "Jonas" } },
      { name: "a.mp4", saved_as: "", error: "a.mp4 is already in the inbox as AcmeSync.mp4", kind: "in_inbox",
        same_as: "AcmeSync.mp4", duplicate_of: null },
      { name: "b.mp4", saved_as: "", error: "b.mp4 is already in the inbox as Gateway.mp4", kind: "in_inbox",
        same_as: "Gateway.mp4", duplicate_of: null }] }));
    expect(screen.getByText("nothing saved · skipped 3")).toHaveClass("error");
    expect(screen.getByText("Skipped: already in the inbox (2)")).toBeInTheDocument();
    expect(screen.getByText("as Gateway.mp4")).toBeInTheDocument();
    expect(screen.getByText("Skipped: processed already (1)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open output/Jonas" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows why a file was refused, and when the upload did not arrive", async () => {
    const { container } = renderInbox([]);
    const zone = container.querySelector(".dropzone")!;
    fireEvent.dragOver(zone);
    expect(zone).toHaveClass("dragging");
    fireEvent.dragLeave(zone);
    expect(zone).not.toHaveClass("dragging");
    fireEvent.drop(zone, { dataTransfer: { files: [new File(["x"], "notes.txt")] } });
    await act(async () => {});
    await act(async () => FakeXMLHttpRequest.latest().respond(200,
      [{ name: "notes.txt", saved_as: "", error: "notes.txt is not a video" }]));
    expect(screen.getByText("Not taken (1)")).toHaveClass("error");
    expect(screen.getByText("notes.txt is not a video")).toBeInTheDocument();
    fireEvent.drop(zone, { dataTransfer: { files: [recording] } });
    await act(async () => {});
    await act(async () => FakeXMLHttpRequest.latest().failToConnect());
    expect(screen.getByText("the upload did not reach the server")).toHaveClass("error");
    fireEvent.drop(zone, { dataTransfer: { files: [] } });
    await act(async () => {});
    expect(FakeXMLHttpRequest.instances).toHaveLength(2);
  });

  it("saves a dropped folder with each file's path, each recording its own, asking nothing defect", async () => {
    // Defect: an upload of several files waited in a dialog asking which were one meeting, at every upload
    // Fix: each recording goes into the inbox as its own; recordings that are one meeting are merged afterwards
    installServer({ "GET /api/inbox/trash": [] });
    const { container, onChanged } = renderInbox([]);
    const asFile = (name: string, path: string) => ({ isFile: true, isDirectory: false, fullPath: path,
      file: (resolve: (file: File) => void) => resolve(new File(["v"], name)) });
    let batches = [[asFile("part 1.mp4", "/Weekly/part 1.mp4"), asFile("part 2.mp4", "/Weekly/part 2.mp4")], []];
    const folder = { isFile: false, isDirectory: true, fullPath: "/Weekly",
      createReader: () => ({ readEntries: (resolve: (entries: unknown[]) => void) => resolve(batches.shift() ?? []) }) };
    fireEvent.drop(container.querySelector(".dropzone")!, { dataTransfer: { files: [], items: [
      { webkitGetAsEntry: () => folder }, { webkitGetAsEntry: () => asFile("Jonas.mp4", "/Jonas.mp4") }] } });
    await act(async () => {});
    await act(async () => {});
    const request = FakeXMLHttpRequest.latest();
    expect(request.url).toBe("/api/inbox/upload-bundle");
    expect(request.sent?.getAll("paths")).toEqual(["Weekly/part 1.mp4", "Weekly/part 2.mp4", "Jonas.mp4"]);
    expect(screen.getByText("3 files")).toBeInTheDocument();
    await act(async () => request.respond(200, { added: ["part 1.mp4", "part 2.mp4", "Jonas.mp4"], refused: [] }));
    expect(screen.getByText("saved part 1.mp4, part 2.mp4, Jonas.mp4")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
    batches = [];
  });

  it("ends an upload whose answer is not the list it expects with an error, never at its last percentage", async () => {
    const { container } = renderInbox([]);
    await userEvent.upload(container.querySelector<HTMLInputElement>("input[type=file]")!, recording);
    await act(async () => FakeXMLHttpRequest.latest().respond(200, "<html>proxy page</html>"));
    expect(screen.getByText("200: the server's answer was not readable")).toHaveClass("error");
  });

  it("takes a file dropped anywhere on the panel, covering the panel with the upload zone while it is dragged defect", async () => {
    // Defect: a file dropped outside the small upload box was opened by the browser instead of uploaded.
    // Fix: the whole panel is the drop target; while a file is dragged over it, an upload zone covers the panel.
    installServer({ "GET /api/inbox/trash": [] });
    const { container, onMessage } = renderInbox([NEW_ITEM]);
    const list = container.querySelector(".tab-body")!;
    fireEvent.dragOver(list, { dataTransfer: { types: ["Files"], files: [], items: [] } });
    expect(document.querySelector(".panel-dropzone")).toHaveTextContent("Drop to upload into the inbox");
    const unreadable = { isFile: true, isDirectory: false, fullPath: "/Gone.mp4",
      file: (_resolve: unknown, reject: (error: unknown) => void) => reject(new Error("the file is gone")) };
    fireEvent.drop(list, { dataTransfer: { types: ["Files"], files: [], items: [{ webkitGetAsEntry: () => unreadable }] } });
    expect(document.querySelector(".panel-dropzone")).toBeNull();
    await vi.waitFor(() => expect(onMessage).toHaveBeenCalledWith("The drop could not be read: the file is gone", true));
  });

  it("shows the upload by default and the search and filter only behind the search button defect", async () => {
    // Defect: the upload box and the search with its filter stood at once and took the room the list needs.
    // Fix: the upload by default, 🔍 beside it opens the search and the filter in its place, closing it clears them.
    installServer({ "GET /api/inbox/trash": [] });
    const { container } = renderInbox([NEW_ITEM, DONE_ITEM]);
    expect(container.querySelector(".dropzone")).not.toBeNull();
    expect(screen.queryByRole("searchbox", { name: "find a video or a run" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "search and filter" }));
    expect(container.querySelector(".dropzone")).toBeNull();
    await userEvent.type(screen.getByRole("searchbox", { name: "find a video or a run" }), "Acme");
    expect(screen.queryByText("Gateway.mp4")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "close the search" }));
    expect(container.querySelector(".dropzone")).not.toBeNull();
    expect(screen.getByText("Gateway.mp4")).toBeInTheDocument();
  });

  it("says when a dropped file cannot be read", async () => {
    installServer({ "GET /api/inbox/trash": [] });
    const { container, onMessage } = renderInbox([]);
    const unreadable = { isFile: true, isDirectory: false, fullPath: "/Gone.mp4",
      file: (_resolve: unknown, reject: (error: unknown) => void) => reject(new Error("the file is gone")) };
    fireEvent.drop(container.querySelector(".dropzone")!, { dataTransfer: { files: [], items: [{ webkitGetAsEntry: () => unreadable }] } });
    await vi.waitFor(() => expect(onMessage).toHaveBeenCalledWith("The drop could not be read: the file is gone", true));
  });

  it("merges two ticked new recordings into one, and says why it cannot merge anything else", async () => {
    const parts = [makeInboxItem("Weekly part 2.mp4", "new"), makeInboxItem("Weekly part 10.mp4", "new"),
      makeInboxItem("Jonas.m4a", "new"), makeInboxItem("guide.pdf", "new")];
    const calls = installServer({ "GET /api/inbox/trash": [],
      "POST /api/inbox/merge": { video: "/work/inbox/Weekly", name: "Weekly" } });
    const { onMessage, onChanged } = renderInbox([...parts, STARTED_ITEM]);
    const merge = screen.getByRole("button", { name: "merge into one recording" });
    expect(merge).toBeDisabled();
    expect(merge).toHaveAttribute("title", "tick two or more recordings to merge them into one");
    await userEvent.click(screen.getByRole("checkbox", { name: "tick Weekly part 2.mp4" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "tick Jonas.m4a" }));
    expect(merge).toHaveAttribute("title", "a video and an audio recording are not merged together");
    await userEvent.click(screen.getByRole("checkbox", { name: "tick Jonas.m4a" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "tick guide.pdf" }));
    expect(merge).toHaveAttribute("title", "a PDF is not merged: only videos or audio recordings");
    await userEvent.click(screen.getByRole("checkbox", { name: "tick guide.pdf" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "tick Weekly part 10.mp4" }));
    expect(merge).toBeEnabled();
    await userEvent.click(merge);
    // the parts in the order they play, part 2 before part 10, and the name they share
    expect(screen.getByRole("list", { name: "parts in the order they are played" })).toHaveTextContent(
      /Weekly part 2\.mp4.*Weekly part 10\.mp4/);
    expect(screen.getByRole("textbox", { name: "name of the merged recording" })).toHaveValue("Weekly");
    await userEvent.click(screen.getByRole("button", { name: /^Merge$/ }));
    expect(calls.find((call) => call.url === "/api/inbox/merge")?.body).toEqual({
      videos: ["/work/inbox/Weekly part 2.mp4", "/work/inbox/Weekly part 10.mp4"], name: "Weekly" });
    expect(onMessage).toHaveBeenCalledWith("Merged 2 recordings into Weekly");
    expect(onChanged).toHaveBeenCalled();
  });

  it("refuses to merge a recording a run has used, and keeps the dialog open with the server's refusal", async () => {
    installServer({ "GET /api/inbox/trash": [],
      "POST /api/inbox/merge": answerJson({ detail: "Weekly 1.mp4 has a job queued or running: it is not merged" }, 400) });
    renderInbox([makeInboxItem("Weekly 1.mp4", "new"), makeInboxItem("Weekly 2.mp4", "new"), STARTED_ITEM]);
    await userEvent.click(screen.getByRole("checkbox", { name: "tick Weekly 1.mp4" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "tick Jonas-call.mp4" }));
    expect(screen.getByRole("button", { name: "merge into one recording" })).toHaveAttribute("title",
      "only recordings no run has used yet are merged");
    await userEvent.click(screen.getByRole("checkbox", { name: "tick Jonas-call.mp4" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "tick Weekly 2.mp4" }));
    await userEvent.click(screen.getByRole("button", { name: "merge into one recording" }));
    const name = screen.getByRole("textbox", { name: "name of the merged recording" });
    await userEvent.clear(name);
    expect(screen.getByRole("button", { name: /^Merge$/ })).toBeDisabled();
    await userEvent.type(name, "Weekly");
    await userEvent.click(screen.getByRole("button", { name: /^Merge$/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Weekly 1.mp4 has a job queued or running");
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("sends a zip picked alone to be unpacked, and shows what it skipped and a refused zip", async () => {
    installServer({ "GET /api/inbox/trash": [] });
    const { container } = renderInbox([]);
    const picker = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    await userEvent.upload(picker, new File(["z"], "bundle.zip", { type: "application/zip" }));
    await act(async () => FakeXMLHttpRequest.latest().respond(200, { added: ["a.mp4"], refused: [
      { name: "notes.vtt", saved_as: "", error: "no recording of the same name came with this transcript" }] }));
    expect(screen.getByText("saved a.mp4 · skipped 1")).toHaveClass("error");
    expect(screen.getByText("no recording of the same name came with this transcript")).toBeInTheDocument();
    await userEvent.upload(picker, new File(["zip"], "bundle.zip", { type: "application/zip" }));
    const request = FakeXMLHttpRequest.latest();
    expect(request.url).toBe("/api/inbox/upload-bundle");
    await act(async () => request.respond(400, { detail: "nothing in the upload is a video" }));
    expect(screen.getByText("400: nothing in the upload is a video")).toHaveClass("error");
  });

  it("the browse picker offers a pdf beside videos and audio defect", () => {
    // The picker listed videos and audio only, so a pdf could not be chosen though the server takes it
    installServer({ "GET /api/inbox/trash": [] });
    const { container } = renderInbox([]);
    const picker = container.querySelector<HTMLInputElement>('input[type="file"]')!;
    expect(picker.accept.split(",")).toContain(".pdf");
  });

  it("opens the file picker from the box by click or keyboard", async () => {
    const { container } = renderInbox([]);
    const picker = container.querySelector<HTMLInputElement>("input[type=file]")!;
    const onPick = vi.fn();
    picker.addEventListener("click", onPick);
    const zone = screen.getByRole("button", { name: /Drop recordings/ });
    await userEvent.click(zone);
    fireEvent.keyDown(zone, { key: "Enter" });
    fireEvent.keyDown(zone, { key: " " });
    fireEvent.keyDown(zone, { key: "a" });
    expect(onPick).toHaveBeenCalledTimes(3);
  });
});

const ARCHIVED = {
  name: "Old.mp4", is_folder: false, size_bytes: 5_000_000, video: "/work/inbox/Old.mp4",
  runs: [{ root: "output", name: "Old", status: "complete", is_live: false, has_document: true, topic: "Pricing rules", display_name: "", stopped_phase: 0 }],
  state: "done", topic: "Pricing rules",
};

describe("InboxPanel reset", () => {
  it("names phases 3 and 4 in the reset list as the ticked Fast run names them, and as the general list when the ticked runs differ defect", async () => {
    installServer({ "GET /api/inbox/trash": [], "GET /api/inbox/archive": [] });
    const titles = { "3": "processing and draft writing", "4": "document reshaping" };
    const fast = makeRun({ root: "output", name: "Acme-fast", video: "", status: "incomplete", phase_titles: titles });
    const other = makeRun({ root: "output", name: "Acme-quality", video: "", status: "incomplete" });
    renderInbox([], [fast, other]);
    await userEvent.click(screen.getByRole("checkbox", { name: "tick Acme-fast" }));
    await userEvent.click(screen.getByRole("button", { name: "reset to phase" }));
    const names = () => [...within(screen.getByRole("dialog")).getByRole("combobox").querySelectorAll("option")].map((option) => option.textContent);
    expect(names()).toContain("3 · processing and draft writing");
    expect(names()).toContain("4 · document reshaping");
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "tick Acme-quality" }));
    await userEvent.click(screen.getByRole("button", { name: "reset to phase" }));
    expect(names()).toContain("3 · frames processing");
    expect(names()).toContain("4 · document writing");
  });

  it("resets every ticked video's run and ticked run folder to the chosen phase, and names a refused one", async () => {
    const calls = installServer({ "GET /api/inbox/trash": [], "GET /api/inbox/archive": [],
      "POST /api/runs/knowledge/Jonas-call/reset": { exit_code: 0, output: "reset-to 2 knowledge/Jonas-call\n  03_notes.md\n" },
      "POST /api/runs/output/Acme-live/reset": { exit_code: 1, output: "Acme-live is running\n" } });
    const loose = makeRun({ root: "output", name: "Acme-live", video: "", status: "incomplete" });
    const { onMessage, onChanged } = renderInbox([NEW_ITEM, STARTED_ITEM], [loose]);
    expect(screen.getByRole("button", { name: "reset to phase" })).toBeDisabled();
    await tick("Gateway.mp4");
    expect(screen.getByRole("button", { name: "reset to phase" })).toBeDisabled();
    await tick("Jonas-call.mp4");
    await userEvent.click(screen.getByRole("checkbox", { name: "tick Acme-live" }));
    await userEvent.click(screen.getByRole("button", { name: "reset to phase" }));
    const dialog = screen.getByRole("dialog");
    expect([...dialog.querySelectorAll(".listing-names li")].map((row) => row.textContent)).toEqual(["Jonas-call", "Acme-live"]);
    expect([...within(dialog).getByRole("combobox").querySelectorAll("option")].map((option) => option.value))
      .toEqual(["0", "1", "2", "3", "4", "5"]);
    await userEvent.selectOptions(within(dialog).getByRole("combobox"), "2");
    await userEvent.click(within(dialog).getByRole("button", { name: "Reset 2" }));
    expect(calls.filter((call) => call.url.endsWith("/reset")).map((call) => [call.url, call.body])).toEqual([
      ["/api/runs/knowledge/Jonas-call/reset", { kind: "phase", number: 2, is_confirmed: true }],
      ["/api/runs/output/Acme-live/reset", { kind: "phase", number: 2, is_confirmed: true }],
    ]);
    expect(onMessage).toHaveBeenLastCalledWith("Reset 1 run to phase 2; Acme-live: Acme-live is running", true);
    expect(onChanged).toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("InboxPanel archive", () => {
  it("ticks a run folder no inbox video owns, archives it whole, and never offers to process it", async () => {
    const calls = installServer({ "GET /api/inbox/trash": [], "GET /api/inbox/archive": [],
      "POST /api/inbox/archive": [{ video: "output/Jonas-comparison", moved_as: "Jonas-comparison", error: "" }] });
    const loose = makeRun({ root: "output", name: "Jonas-comparison", video: "", status: "incomplete" });
    const { onMessage } = renderInbox([NEW_ITEM], [loose]);
    await userEvent.click(screen.getByRole("checkbox", { name: "tick Jonas-comparison" }));
    expect(screen.getByRole("checkbox", { name: "tick Jonas-comparison" }).closest("li")).toHaveClass("ticked");
    // a ticked run folder naming no recording has nothing to process: the button says so
    expect(screen.getByRole("button", { name: /No element selected/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "archive" }));
    expect(calls.find((call) => call.url === "/api/inbox/archive" && call.method === "POST")?.body)
      .toEqual({ videos: [], run_folders: ["output/Jonas-comparison"] });
    expect(onMessage).toHaveBeenLastCalledWith("Archived 1: listed under Archive with its runs", false);
  });

  it("asks before trashing a run folder that is not finished, and the section's box ticks its run folders too", async () => {
    const calls = installServer({ "GET /api/inbox/trash": [], "GET /api/inbox/archive": [],
      "POST /api/inbox/trash": [{ video: "output/Jonas-comparison", moved_as: "Jonas-comparison", error: "" }] });
    const loose = makeRun({ root: "output", name: "Jonas-comparison", video: "", status: "incomplete" });
    const { onMessage } = renderInbox([], [loose]);
    await userEvent.click(screen.getByRole("checkbox", { name: "select every video in Not finished" }));
    await userEvent.click(screen.getByRole("button", { name: "move to trash" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("This file is not processed yet. Are you sure?");
    expect(dialog).toHaveTextContent("not finished · run folder");
    await userEvent.click(within(dialog).getByRole("button", { name: /Move to trash/ }));
    expect(calls.find((call) => call.url === "/api/inbox/trash" && call.method === "POST")?.body)
      .toEqual({ videos: [], run_folders: ["output/Jonas-comparison"] });
    expect(onMessage).toHaveBeenLastCalledWith("Moved 1 to inbox/.trash.", false);
  });

  it("names a moved run folder as one, and brings it back to its run folders", async () => {
    installServer({ "GET /api/inbox/trash": [{ name: "Jonas-comparison", is_folder: true, size_bytes: 2_000, video: "",
      run_folder: "/work/output/Jonas-comparison" }], "GET /api/inbox/archive": [],
      "POST /api/inbox/trash/restore": [{ name: "Jonas-comparison", restored_as: "Jonas-comparison", error: "" }] });
    const { onMessage } = renderInbox();
    await userEvent.click(await screen.findByRole("button", { name: /Trash/ }));
    expect(screen.getByText(/^run folder ·/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Restore/ }));
    expect(onMessage).toHaveBeenLastCalledWith("Restored Jonas-comparison to its run folders", false);
  });

  it("archives the ticked videos without asking, and reports a refusal", async () => {
    let answer: unknown = [{ video: NEW_ITEM.video, moved_as: "Gateway.mp4", error: "" }];
    const calls = installServer({ "GET /api/inbox/trash": [], "GET /api/inbox/archive": [], "POST /api/inbox/archive": () => answer });
    const { onMessage, onChanged } = renderInbox();
    expect(screen.getByRole("button", { name: "archive" })).toBeDisabled();
    await tick("Gateway.mp4");
    await userEvent.click(screen.getByRole("button", { name: "archive" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(calls.find((call) => call.url === "/api/inbox/archive" && call.method === "POST")?.body)
      .toEqual({ videos: [NEW_ITEM.video], run_folders: [] });
    expect(onMessage).toHaveBeenLastCalledWith("Archived 1: listed under Archive with its runs", false);
    expect(onChanged).toHaveBeenCalled();
    answer = [{ video: NEW_ITEM.video, moved_as: "", error: "Gateway.mp4 is not a recording in the inbox" }];
    await tick("Gateway.mp4");
    await userEvent.click(screen.getByRole("button", { name: "archive" }));
    expect(onMessage).toHaveBeenLastCalledWith("Gateway.mp4 is not a recording in the inbox", true);
    answer = answerJson({ detail: "locked" }, 423);
    await tick("Gateway.mp4");
    await userEvent.click(screen.getByRole("button", { name: "archive" }));
    expect(onMessage).toHaveBeenLastCalledWith("423: locked", true);
  });

  it("lists the archive with each entry's topic and run, opens the run, and Unarchive brings it back", async () => {
    let restoreAnswer: unknown = [{ name: "Old.mp4", restored_as: "Old.mp4", error: "" }];
    const calls = installServer({
      "GET /api/inbox/trash": [],
      "GET /api/inbox/archive": [ARCHIVED, { ...ARCHIVED, name: "Gateway-meeting", is_folder: true, size_bytes: 2_000_000_000,
        video: "/work/inbox/Gateway-meeting", runs: [], state: "new", topic: "" }],
      "POST /api/inbox/archive/restore": () => restoreAnswer,
    });
    const { onMessage, onChanged, onOpenRun } = renderInbox([NEW_ITEM]);
    const head = await screen.findByRole("button", { name: /^Archive\s*2/ });
    expect(head).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(head);
    expect(readViewValue("inboxSections", [])).toContain("archive");
    expect(screen.getByText("Pricing rules")).toHaveClass("topic");
    expect(screen.getByText("meeting folder · 2.0 GB")).toBeInTheDocument();
    const row = screen.getByText("Old.mp4").closest("li")!;
    await userEvent.click(within(row).getByRole("button", { name: /^done ›/ }));
    expect(onOpenRun).toHaveBeenCalledWith("output", "Old");
    expect(within(screen.getByText("Gateway-meeting").closest("li")!).getByText("no run")).toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: /Unarchive/ }));
    expect(calls.find((call) => call.url === "/api/inbox/archive/restore")?.body).toEqual({ names: ["Old.mp4"] });
    expect(onMessage).toHaveBeenLastCalledWith("Moved Old.mp4 back into the inbox", false);
    expect(onChanged).toHaveBeenCalled();
    restoreAnswer = [{ name: "Old.mp4", restored_as: "", error: "Old.mp4 is not in the archive" }];
    await userEvent.click(within(row).getByRole("button", { name: /Unarchive/ }));
    expect(onMessage).toHaveBeenLastCalledWith("Old.mp4 is not in the archive", true);
    restoreAnswer = answerJson({ detail: "locked" }, 423);
    await userEvent.click(within(row).getByRole("button", { name: /Unarchive/ }));
    expect(onMessage).toHaveBeenLastCalledWith("423: locked", true);
  });

  it("keeps an archived video's run out of the other sections", async () => {
    installServer({ "GET /api/inbox/trash": [], "GET /api/inbox/archive": [ARCHIVED] });
    renderInbox([NEW_ITEM], [makeRun({ root: "output", name: "Old", video: "/work/inbox/Old.mp4", has_document: true, status: "complete" })]);
    await screen.findByRole("button", { name: /^Archive\s*1/ });
    expect(screen.queryByRole("button", { name: /^Done/ })).not.toBeInTheDocument();
  });

  it("says when the archive cannot be read, and shows no archive", async () => {
    installServer({ "GET /api/inbox/trash": [], "GET /api/inbox/archive": answerJson({ detail: "no" }, 500) });
    const { onMessage } = renderInbox();
    await vi.waitFor(() => expect(onMessage).toHaveBeenCalledWith("The archive could not be read: 500: no", true));
    expect(screen.queryByRole("button", { name: /^Archive/ })).not.toBeInTheDocument();
  });

  it("opens the Archive's arrow and counts only the matches while a search finds an archived entry", async () => {
    installServer({ "GET /api/inbox/trash": [], "GET /api/inbox/archive": [ARCHIVED, { ...ARCHIVED, name: "Other.mp4", topic: "" }] });
    renderInbox([NEW_ITEM]);
    const head = await screen.findByRole("button", { name: /^Archive\s*2/ });
    await userEvent.click(screen.getByRole("button", { name: "search and filter" }));
    await userEvent.type(screen.getByRole("searchbox", { name: "find a video or a run" }), "old");
    expect(head).toHaveTextContent(/^Archive\s*1/);
    expect(head.querySelector(".caret")).toHaveClass("open");
  });
});
