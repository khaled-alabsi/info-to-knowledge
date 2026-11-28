import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "./App";
import type { InboxItem, Job, RunSummary } from "./api";
import { FakeXMLHttpRequest, PHASES, answerJson, installServer, makeInboxItem, makeJob, makeRun } from "./test/fakeServer";
import { readViewValue, saveViewValue } from "./viewState";

function serveLists({ inbox = [makeInboxItem("AcmeSync-demo.mp4", "done"), makeInboxItem("Gateway.mp4", "new")],
  runs = [makeRun(), makeRun({ name: "Jonas-call", folder: "/work/knowledge/Jonas-call", is_live: true, status: "running" })],
  jobs = [makeJob(), makeJob({ job_id: "job-2", status: "running" }), makeJob({ job_id: "job-0", status: "finished", exit_code: 0 })],
  extra = {},
}: { inbox?: InboxItem[]; runs?: RunSummary[]; jobs?: Job[]; extra?: Parameters<typeof installServer>[0] } = {}) {
  return installServer({
    ...extra,
    "GET /api/phases": PHASES,
    "GET /api/inbox": inbox,
    "GET /api/runs": runs,
    "GET /api/jobs": jobs,
    "GET /api/inbox/trash": [],
    "POST /api/jobs/job-1/cancel": makeJob({ status: "cancelled" }),
    "POST /api/jobs": answerJson({ detail: "queue full" }, 409),
  });
}

describe("App", () => {
  it("opens the running run from the top bar's running count menu", async () => {
    serveLists();
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /1 running/ }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /Jonas-call/ }));
    expect(await screen.findByRole("heading", { level: 2, name: "Jonas-call" })).toBeInTheDocument();
  });

  it("with several runs running, the running count lists each with its phase and provider set, and opens the one picked defect", async () => {
    // Defect: "3 running" opened whichever running run came first, so the owner could not choose which one.
    // Fix: with more than one running, the count opens a menu naming each run, its phase and its provider set.
    serveLists({ runs: [
      makeRun({ name: "Jonas-call", folder: "/work/knowledge/Jonas-call", is_live: true, status: "running", live_phase: 3 }),
      makeRun({ name: "Weekly", folder: "/work/knowledge/Weekly", is_live: true, status: "running", live_phase: 4, provider_set: "gpu-box" }),
    ] });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /2 running/ }));
    expect(screen.getByRole("heading", { level: 2, name: "Nothing open" })).toBeInTheDocument();
    expect(await screen.findByText("phase 3 · provider set default")).toBeInTheDocument();
    expect(screen.getByText("phase 4 · provider set gpu-box")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("menuitem", { name: /Weekly/ }));
    expect(await screen.findByRole("heading", { level: 2, name: "Weekly" })).toBeInTheDocument();
  });

  it("shows the signed-in account with Sign out, and hides the settings and the Admin tab from a viewer", async () => {
    serveLists();
    const onSignOut = vi.fn();
    const { rerender } = render(<App account={{ name: "Lea", role: "viewer" }} onSignOut={onSignOut} />);
    expect(await screen.findByText(/Lea · viewer/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Settings" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Admin/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(onSignOut).toHaveBeenCalled();

    rerender(<App account={{ name: "Ada", role: "admin" }} onSignOut={onSignOut} />);
    expect(screen.getByRole("button", { name: "Settings" })).toBeInTheDocument();
    installServer({ "GET /api/accounts": { accounts: [{ name: "Ada", role: "admin", created: "" }] } });
    await userEvent.click(screen.getByRole("tab", { name: /Manager/ }));
    await userEvent.click(screen.getByRole("button", { name: /Users/ }));
    expect(await screen.findByRole("heading", { level: 2, name: "Users" })).toBeInTheDocument();
  });

  it("shows the runs and inbox items of the project chosen in the top bar", async () => {
    serveLists({ runs: [makeRun(), makeRun({ name: "Gateway", folder: "/work/knowledge/Gateway", project: "acme" })],
      extra: { "GET /api/projects": { projects: [{ slug: "default", title: "Default", runs: 0, role: "admin" },
        { slug: "acme", title: "Acme", runs: 1, role: "admin" }], groups: [] } } });
    render(<App />);
    expect(await screen.findByText("Gateway.mp4")).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "All projects" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: "Acme" }));
    expect(screen.getByText("Gateway.mp4")).toBeInTheDocument();
    expect(screen.queryByText("AcmeSync-demo.mp4")).not.toBeInTheDocument();
  });

  it("says when the server does not answer", async () => {
    render(<App />);
    expect(await screen.findByText("server not answering")).toBeInTheDocument();
  });

  it("says it is loading until the lists arrive, never that the inbox is empty or that nothing is open", async () => {
    let answerInbox: (value: unknown) => void = () => undefined;
    installServer({
      "GET /api/phases": PHASES,
      "GET /api/inbox": () => new Promise((resolve) => { answerInbox = resolve as (value: unknown) => void; }),
      "GET /api/runs": [],
      "GET /api/jobs": [],
      "GET /api/inbox/trash": [],
    });
    render(<App />);
    expect(screen.getAllByText("Loading…").length).toBe(2);
    expect(screen.queryByText("The inbox is empty. Drop a recording above.")).not.toBeInTheDocument();
    expect(screen.queryByText("Nothing open")).not.toBeInTheDocument();
    await act(async () => answerInbox([]));
    expect(await screen.findByText("The inbox is empty. Drop a recording above.")).toBeInTheDocument();
    expect(screen.getByText("Nothing open")).toBeInTheDocument();
  });

  it("counts new inbox videos, live runs and queued jobs", async () => {
    serveLists();
    render(<App />);
    expect(await screen.findByText("1 running")).toBeInTheDocument();
    expect(screen.getByText("1 queued")).toBeInTheDocument();
    expect(within(screen.getByRole("tab", { name: /Inbox/ })).getByText("1")).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /Runs/ })).not.toBeInTheDocument();
    expect(within(screen.getByRole("tab", { name: /Queue/ })).getByText("1")).toBeInTheDocument();
    expect(screen.getByText("Nothing open")).toBeInTheDocument();
  });

  it("the Settings button, last in the top bar, shows its view alone, without the sidebar, and closes it again", async () => {
    serveLists();
    const { container } = render(<App />);
    expect(screen.queryByRole("tab", { name: /Settings/ })).not.toBeInTheDocument();
    const settings = await screen.findByRole("button", { name: "Settings" });
    expect(container.querySelector(".top-stats")?.lastElementChild).toBe(settings);
    // The owner: the icon is enough, a little smaller; open with nothing to save, it says how to leave
    // ("× Close settings", the owner's "exit settings, or something better")
    expect(settings).toHaveTextContent(/^$/);
    expect(settings.querySelector("svg")).toHaveAttribute("width", "14");
    await userEvent.click(settings);
    expect(settings).not.toHaveAttribute("aria-pressed");
    expect(settings).toHaveTextContent("Close settings");
    expect(await screen.findByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(container.querySelector(".shell")).toHaveClass("shows-settings");
    // The sidebar showed an empty Inbox and Queue beside the settings: it is gone while they are open
    expect(container.querySelector(".sidebar")).toBeNull();
    await userEvent.click(settings);
    expect(container.querySelector(".shell")).not.toHaveClass("shows-settings");
    expect(screen.getByRole("tab", { name: /Inbox/ })).toHaveAttribute("aria-selected", "true");
  });

  it("opens a run no inbox video owns from the section of its state, and goes back", async () => {
    const user = userEvent.setup();
    serveLists();
    render(<App />);
    await user.click(await screen.findByTitle(/^Jonas-call/));
    expect(screen.getByRole("heading", { level: 2, name: "Jonas-call" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "back" }));
    expect(screen.getByText("Nothing open")).toBeInTheDocument();
  });

  it("opens a run the list did not show yet once the list is read again, and says when the server lists no such run", async () => {
    const user = userEvent.setup();
    let runs = [makeRun()];
    installServer({ "GET /api/phases": PHASES, "GET /api/inbox": [], "GET /api/runs": () => runs, "GET /api/jobs": [],
      "GET /api/inbox/trash": [], "GET /api/inbox/archive": [] });
    const { container } = render(<App />);
    await screen.findByText("The inbox is empty. Drop a recording above.");
    const refuse = async (name: string) => {
      await user.upload(container.querySelector<HTMLInputElement>("input[type=file]")!, new File(["v"], `${name}.mp4`, { type: "video/mp4" }));
      await act(async () => FakeXMLHttpRequest.latest().respond(200, [{ name: `${name}.mp4`, saved_as: "", error: "processed already",
        kind: "processed", same_as: `${name}.mp4`, duplicate_of: { root: "output", name } }]));
    };
    await refuse("Weekly");
    runs = [makeRun(), makeRun({ root: "output", name: "Weekly", folder: "/work/output/Weekly" })];
    await user.click(screen.getByRole("button", { name: "Open output/Weekly" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Weekly" })).toBeInTheDocument();
    await refuse("Gone");
    await user.click(screen.getByRole("button", { name: "Open output/Gone" }));
    expect(await screen.findByText("output/Gone is not in the run list")).toBeInTheDocument();
  });

  it("keeps saving what is open after a refresh whose saved job is gone from an empty queue", async () => {
    const user = userEvent.setup();
    saveViewValue("opened", { kind: "job", id: "job-9" });
    serveLists({ jobs: [] });
    render(<App />);
    await user.click(await screen.findByTitle(/^Jonas-call/));
    expect(readViewValue("opened", null)).toEqual({ kind: "run", root: "knowledge", name: "Jonas-call" });
  });

  it("says when the setup check cannot be read from the Settings page's Setup wizard button", async () => {
    const user = userEvent.setup();
    installServer({ "GET /api/phases": PHASES, "GET /api/inbox": [], "GET /api/runs": [], "GET /api/jobs": [], "GET /api/inbox/trash": [],
      "GET /api/settings": [], "GET /api/profiles": { profiles: [] }, "GET /api/setup": answerJson({ detail: "check failed" }, 500) });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Settings" }));
    await user.click(screen.getByRole("button", { name: /Setup wizard/ }));
    expect(await screen.findByText("The setup check could not be read: 500: check failed")).toBeInTheDocument();
  });

  it("in a container never opens the setup wizard, and names the missing model providers on the Settings page", async () => {
    const user = userEvent.setup();
    installServer({ "GET /api/phases": PHASES, "GET /api/inbox": [], "GET /api/runs": [], "GET /api/jobs": [], "GET /api/inbox/trash": [],
      "GET /api/settings": [], "GET /api/profiles": { profiles: [] },
      "GET /api/setup": { is_container: true, has_models: false, runtimes: [], models: [], download: { is_running: false, log: "" },
        recommended: [], missing_providers: [{ name: "frame", used_by: ["frames_processing"],
          reason: "no model is set for it: add models.frame in Settings" }] } });
    render(<App />);
    const gear = await screen.findByRole("button", { name: "Settings: provider missing" });
    expect(screen.queryByRole("dialog", { name: "Setup" })).not.toBeInTheDocument();
    await user.click(gear);
    expect(await screen.findByText("A model provider a run needs is not set up.")).toBeInTheDocument();
    expect(screen.getByText("no model is set for it: add models.frame in Settings", { exact: false })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Setup wizard|Setup needed/ })).not.toBeInTheDocument();
  });

  it("outside a container opens the setup wizard by itself when no model is configured", async () => {
    installServer({ "GET /api/phases": PHASES, "GET /api/inbox": [], "GET /api/runs": [], "GET /api/jobs": [], "GET /api/inbox/trash": [],
      "GET /api/setup": { is_container: false, has_models: false, runtimes: [], models: [], download: { is_running: false, log: "" },
        recommended: [], missing_providers: [] } });
    render(<App />);
    expect(await screen.findByText("No model is configured.")).toBeInTheDocument();
  });

  it("keeps what was chosen on a new video's page when the video is renamed", async () => {
    const user = userEvent.setup();
    installServer({ "GET /api/phases": PHASES, "GET /api/inbox": [makeInboxItem("Weekly.mp4", "new")], "GET /api/runs": [],
      "GET /api/jobs": [], "GET /api/inbox/trash": [], "GET /api/inbox/archive": [], "GET /api/profiles": { profiles: [] },
      "POST /api/inbox/rename": { video: "/work/inbox/Weekly 2.mp4", name: "Weekly 2.mp4" } });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^new › open the page of Weekly.mp4/ }));
    await user.click(screen.getByRole("radio", { name: "Audio only" }));
    await user.click(screen.getByRole("button", { name: "rename" }));
    await user.type(screen.getByRole("textbox", { name: "new name" }), " 2{Enter}");
    expect(await screen.findByRole("heading", { level: 2, name: "Weekly 2.mp4" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Audio only" })).toHaveAttribute("aria-checked", "true");
  });

  it("opens the run of an inbox video from its state pill", async () => {
    const user = userEvent.setup();
    serveLists({ inbox: [makeInboxItem("AcmeSync-demo.mp4", "done"),
      makeInboxItem("Gateway.mp4", "started", { runs: [{ root: "output", name: "gone", status: "incomplete", is_live: false, has_document: false, topic: "", display_name: "", stopped_phase: 0 }] })] });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^not finished ›/ }));
    expect(screen.getByText("Nothing open")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Done/ }));
    await user.click(screen.getByRole("button", { name: /^done ›/ }));
    expect(screen.getByRole("heading", { level: 2, name: "AcmeSync-demo" })).toBeInTheDocument();
  });

  it("opens a job's console from the Queue", async () => {
    const user = userEvent.setup();
    serveLists();
    render(<App />);
    await user.click(screen.getByRole("tab", { name: /Queue/ }));
    await user.click(await screen.findByTitle(/job job-1 · queued/));
    // named as its Queue row names it: the run folder
    expect(screen.getByRole("heading", { level: 2, name: "AcmeSync-demo" })).toBeInTheDocument();
    expect(screen.getByText("job job-1 · every phase")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "back" }));
    expect(screen.getByText("Nothing open")).toBeInTheDocument();
  });

  it("opens a new video's page from its new pill, and its run's page in its place once it started", async () => {
    const user = userEvent.setup();
    let isStarted = false;
    const run = makeRun({ name: "Gateway", folder: "/work/knowledge/Gateway", path: "knowledge/Gateway", is_live: true, status: "running" });
    installServer({
      "GET /api/phases": PHASES, "GET /api/jobs": [], "GET /api/inbox/trash": [],
      "GET /api/profiles": { profiles: [], server_profile: "" },
      "GET /api/inbox": () => [makeInboxItem("Gateway.mp4", isStarted ? "running" : "new")],
      "GET /api/runs": () => (isStarted ? [run] : []),
      "POST /api/jobs": () => {
        isStarted = true;
        return [makeJob()];
      },
    });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^new ›/ }));
    expect(screen.getByRole("heading", { level: 2, name: "Gateway.mp4" })).toBeInTheDocument();
    expect(readViewValue("opened", null)).toEqual({ kind: "video", video: "/work/inbox/Gateway.mp4" });
    await user.click(screen.getByRole("button", { name: /Start run/ }));
    expect(await screen.findByRole("heading", { level: 2, name: "Gateway" })).toBeInTheDocument();
  });

  it("the Manager tab lists its tools, and Download opens in the detail view and comes back after a refresh", async () => {
    const user = userEvent.setup();
    installServer({
      "GET /api/phases": PHASES, "GET /api/inbox": [], "GET /api/runs": [makeRun({ has_document: true })], "GET /api/jobs": [],
      "GET /api/inbox/trash": [], "GET /api/download/parts": [{ key: "document", label: "Final document with its resources folder" }],
    });
    const first = render(<App />);
    await user.click(await screen.findByRole("tab", { name: /Manager/ }));
    expect(screen.queryByRole("heading", { level: 2, name: "Download" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Download/ }));
    expect(await screen.findByRole("heading", { level: 2, name: "Download" })).toBeInTheDocument();
    expect(readViewValue("opened", null)).toEqual({ kind: "tool", tool: "download" });
    first.unmount();
    render(<App />);
    expect(await screen.findByRole("heading", { level: 2, name: "Download" })).toBeInTheDocument();
  });

  it("after a refresh, comes back to the tab and the run that were open", async () => {
    const user = userEvent.setup();
    serveLists();
    const first = render(<App />);
    await user.click(await screen.findByRole("button", { name: /^Done/ }));
    await user.click(screen.getByRole("button", { name: /^done ›/ }));
    expect(readViewValue("opened", null)).toEqual({ kind: "run", root: "knowledge", name: "AcmeSync-demo" });
    await user.click(screen.getByRole("tab", { name: /Queue/ }));
    first.unmount();

    render(<App />);
    expect(await screen.findByRole("heading", { level: 2, name: "AcmeSync-demo" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Queue/ })).toHaveAttribute("aria-selected", "true");
  });

  it("a tab saved by the older page with a Runs tab falls back to the inbox", async () => {
    serveLists();
    saveViewValue("tab", "runs");
    render(<App />);
    expect(await screen.findByRole("tab", { name: /Inbox/ })).toHaveAttribute("aria-selected", "true");
  });

  it("after a refresh, reopens the job that was open, and forgets one that is gone", async () => {
    serveLists();
    saveViewValue("opened", { kind: "job", id: "job-0" });
    const first = render(<App />);
    expect(await screen.findByText("Job console")).toBeInTheDocument();
    first.unmount();
    saveViewValue("opened", { kind: "job", id: "no-such-job" });
    render(<App />);
    expect(await screen.findByText("Nothing open")).toBeInTheDocument();
    await screen.findByText("1 queued");
    expect(readViewValue("opened", "kept")).toBe("kept");
  });

  it("asks for the phase list again on each refresh until it arrives, and never after", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let isRestarting = true;
    const calls = installServer({
      "GET /api/phases": () => (isRestarting ? answerJson({ detail: "restarting" }, 503) : PHASES),
      "GET /api/inbox": [], "GET /api/runs": [], "GET /api/jobs": [], "GET /api/inbox/trash": [],
    });
    const countPhaseCalls = () => calls.filter((call) => call.url === "/api/phases").length;
    render(<App />);
    expect(await screen.findByText("server not answering")).toBeInTheDocument();
    expect(countPhaseCalls()).toBe(1);

    isRestarting = false;
    await act(async () => vi.advanceTimersByTime(3000));
    expect(countPhaseCalls()).toBe(2);
    await act(async () => vi.advanceTimersByTime(3000));
    expect(countPhaseCalls()).toBe(2);
    expect(screen.queryByText("server not answering")).not.toBeInTheDocument();
  });

  it("follows an open run whose folder moved to another root, and closes one that is gone", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    let runs = [makeRun()];
    installServer({
      "GET /api/phases": PHASES, "GET /api/inbox": [], "GET /api/jobs": [], "GET /api/inbox/trash": [],
      "GET /api/runs": () => runs,
    });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /AcmeSync-demo/ }));
    expect(screen.getByText(/^knowledge\/AcmeSync-demo/)).toBeInTheDocument();

    runs = [makeRun({ root: "output", folder: "/work/output/AcmeSync-demo", path: "output/AcmeSync-demo" })];
    await act(async () => vi.advanceTimersByTime(3000));
    expect(await screen.findByText("AcmeSync-demo is now in output/; showing it there")).toBeInTheDocument();
    expect(screen.getByText(/^output\/AcmeSync-demo/)).toBeInTheDocument();

    runs = [makeRun({ name: "Gateway", folder: "/work/output/Gateway", path: "output/Gateway" })];
    await act(async () => vi.advanceTimersByTime(3000));
    expect(await screen.findByText("output/AcmeSync-demo no longer exists")).toBeInTheDocument();
    expect(screen.getByText("Nothing open")).toBeInTheDocument();
  });

  it("shows what an action did for 5 seconds, keeps an error until it is closed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    // a recording no job holds: the queued job's own recording would be left out as already waiting
    serveLists({ inbox: [makeInboxItem("Jonas-call.mp4", "done")] });
    render(<App />);
    await user.click(screen.getByRole("tab", { name: /Queue/ }));
    await user.click(await screen.findByRole("button", { name: "cancel" }));
    expect(await screen.findByText("Job job-1: taken off the queue")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /Inbox/ }));
    await user.click(screen.getByRole("button", { name: "run settings" }));
    await user.click(screen.getByRole("button", { name: /Process every video/ }));
    // a done recording among them is asked about first
    await user.click(within(await screen.findByRole("dialog", { name: "Process done recordings again?" }))
      .getByRole("button", { name: "Process again" }));
    const error = await screen.findByText("Jonas-call.mp4 refused: 409: queue full");
    expect(error.closest(".toast")).toHaveClass("error");

    act(() => vi.advanceTimersByTime(5000));
    expect(screen.queryByText("Job job-1: taken off the queue")).not.toBeInTheDocument();
    // an error is the one message to act on: it stays until its × is pressed
    act(() => vi.advanceTimersByTime(60000));
    expect(error).toBeInTheDocument();
    await user.click(within(error.closest(".toast") as HTMLElement).getByRole("button", { name: "dismiss" }));
    expect(screen.queryByText("Jonas-call.mp4 refused: 409: queue full")).not.toBeInTheDocument();
  });

  it("shows a message said again once, and at most four messages that close by themselves", async () => {
    const user = userEvent.setup();
    serveLists({ inbox: [] });
    render(<App />);
    await screen.findByText("The inbox is empty. Drop a recording above.");
    for (let index = 0; index < 6; index++) {
      await user.click(screen.getByRole("button", { name: "run settings" }));
      await user.click(screen.getByRole("button", { name: /Process every video/ }));
    }
    expect(screen.getAllByText(/Nothing to process/)).toHaveLength(1);
  });
});
