import { useCallback, useEffect, useRef, useState } from "react";
import { buildRunPath, describeError, describeMissingProviders, fetchJson, fetchProjects, isAdmin, OPEN_INSTRUCTION_EVENT, sendJson } from "./api";
import type { Account, InboxItem, InstructionTarget, Job, Phase, Project, RunSummary, SetupCheck } from "./api";
import { Dropdown, Menu } from "./components/Dropdown";
import Icon from "./components/Icon";
import InboxPanel from "./components/InboxPanel";
import JobConsole from "./components/JobConsole";
import DownloadView from "./components/DownloadView";
import RenameView from "./components/RenameView";
import CompressView from "./components/CompressView";
import AccountsView from "./components/AccountsView";
import GroupsView from "./components/GroupsView";
import ProjectsView from "./components/ProjectsView";
import ManagerPanel from "./components/ManagerPanel";
import { loadKnownTags } from "./components/TagInput";
import type { ManagerTool } from "./components/ManagerPanel";
import SettingsView, { saveDrafts } from "./components/SettingsView";
import InstructionsView from "./components/InstructionsView";
import type { SettingDraft, SettingDrafts } from "./components/SettingsView";
import QueuePanel from "./components/QueuePanel";
import RunDetail from "./components/RunDetail";
import VideoDetail from "./components/VideoDetail";
import Toasts from "./components/Toasts";
import { readViewValue, saveViewValue, usePersistentState } from "./viewState";
import type { Toast } from "./components/Toasts";
import Spinner from "./components/Spinner";
import SetupWizard, { hasSetupProblem } from "./components/SetupWizard";

const LIST_REFRESH_MS = 3000;
const TOAST_MS = 5000;
// How long the top bar's Pause queue can hold the next job back
const QUEUE_PAUSE_MINUTES = [30, 60, 120, 240, 360];
// "30 minutes", "1 hour", "2 hours"
function describePauseLength(minutes: number): string {
  if (minutes < 60) return `${minutes} minutes`;
  return minutes === 60 ? "1 hour" : `${minutes / 60} hours`;
}
interface QueuePause {
  until: string;
  is_until_resumed: boolean;
}
const NO_QUEUE_PAUSE: QueuePause = { until: "", is_until_resumed: false };
// "The running job goes on; no next job starts" read as if the pause let the next job go on (the owner)
const QUEUE_PAUSE_NOTE = "Pauses the run that is running now and every job waiting after it.";

type Opened = { kind: "run"; run: RunSummary } | { kind: "job"; job: Job } | { kind: "video"; item: InboxItem } | { kind: "tool"; tool: ManagerTool } | null;
const TABS = ["inbox", "queue", "manager", "settings"] as const;
// The two pages the settings gear opens
const SETTINGS_PAGES = [{ key: "settings", label: "Settings" }, { key: "instructions", label: "Instructions" }] as const;
type SettingsPage = (typeof SETTINGS_PAGES)[number]["key"];
// The top bar's project choice that shows every project the account may see
const ALL_PROJECTS = "all";
const DEFAULT_PROJECT = "default";
type Tab = (typeof TABS)[number];
type SavedOpen = { kind: "run"; root: string; name: string } | { kind: "job"; id: string } | { kind: "video"; video: string } | { kind: "tool"; tool: ManagerTool };

// The width at and under which the open view replaces the lists: the stylesheet's `@media (max-width: 900px)`
const PHONE_QUERY = "(max-width: 900px)";

function isPhoneWidth(): boolean {
  return window.matchMedia(PHONE_QUERY).matches;
}

// What the view cookie keeps of the open view, so a refresh reopens it
function toSavedOpen(opened: Opened): SavedOpen | null {
  switch (opened?.kind) {
    case "run": return { kind: "run", root: opened.run.root, name: opened.run.name };
    case "video": return { kind: "video", video: opened.item.video };
    case "tool": return { kind: "tool", tool: opened.tool };
    case "job": return { kind: "job", id: opened.job.job_id };
    default: return null;
  }
}

// The open view a saved one names, found in the lists read; null when its run, video or job is gone, or when it is of
// a kind an older page wrote
function findSavedOpen(saved: SavedOpen, lists: { runs: RunSummary[]; inbox: InboxItem[]; jobs: Job[] }): Opened {
  switch (saved.kind) {
    case "run": {
      const run = lists.runs.find((candidate) => candidate.root === saved.root && candidate.name === saved.name)
        ?? lists.runs.find((candidate) => candidate.name === saved.name);
      return run ? { kind: "run", run } : null;
    }
    case "video": {
      const item = lists.inbox.find((candidate) => candidate.video === saved.video);
      return item ? { kind: "video", item } : null;
    }
    case "job": {
      const job = lists.jobs.find((candidate) => candidate.job_id === saved.id);
      return job ? { kind: "job", job } : null;
    }
    case "tool": return { kind: "tool", tool: saved.tool };
    default: return null;
  }
}

export default function App({ account = null, onSignOut }: { account?: Account | null; onSignOut?: () => void } = {}) {
  const canAdmin = isAdmin(account);
  const [phases, setPhases] = useState<Phase[]>([]);
  const [inbox, setInbox] = useState<InboxItem[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  // The projects the account may see, and the one the lists show: "all", or one project's slug
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = usePersistentState<string>("project", ALL_PROJECTS, (value) => typeof value === "string");
  const [jobs, setJobs] = useState<Job[]>([]);
  // the person's pause of the queue: when it ends, or held until resumed; both empty when not paused
  const [queuePause, setQueuePause] = useState<QueuePause>(NO_QUEUE_PAUSE);
  // whether the jobs were read once: before, the queue said "Nothing is waiting" over 31 waiting jobs
  const [isJobsLoaded, setIsJobsLoaded] = useState(false);
  const [tab, setTab] = usePersistentState<Tab>("tab", "inbox", (value) => TABS.includes(value as Tab));
  const [opened, setOpened] = useState<Opened>(null);
  // On a phone the open view replaces the list: it starts at its top, and closing it scrolls the list back to where it
  // was. The list's place is kept while the list shows, since once a view opens the list is hidden and the page shorter.
  const listScroll = useRef(0);
  useEffect(() => {
    if (opened) return;
    const keep = () => { listScroll.current = window.scrollY; };
    window.addEventListener("scroll", keep, { passive: true });
    return () => window.removeEventListener("scroll", keep);
  }, [opened]);
  // The keyboard follows: an opened view's title takes the focus, and closing it gives the focus back to what opened it
  const focusBeforeOpen = useRef<HTMLElement | null>(null);
  const wasOpened = useRef(false);
  useEffect(() => {
    const isPhone = isPhoneWidth();
    if (opened && !wasOpened.current) {
      focusBeforeOpen.current = document.activeElement as HTMLElement | null;
      if (isPhone) window.scrollTo(0, 0);
      window.requestAnimationFrame(() => {
        const title = document.querySelector<HTMLElement>(".main h2");
        if (!title) return;
        title.tabIndex = -1;
        title.focus({ preventScroll: true });
      });
    } else if (!opened && wasOpened.current) {
      window.requestAnimationFrame(() => {
        if (isPhone) window.scrollTo(0, listScroll.current);
        if (focusBeforeOpen.current?.isConnected) focusBeforeOpen.current.focus({ preventScroll: true });
      });
    }
    wasOpened.current = Boolean(opened);
  }, [opened]);
  // The view open before a refresh, reopened once the lists have loaded; until then nothing open is saved over it.
  const pendingOpen = useRef<SavedOpen | null | undefined>(readViewValue<SavedOpen | null>("opened", null));
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [isServerDown, setIsServerDown] = useState(false);
  // why the lists could not be read the last time they failed
  const [listsError, setListsError] = useState("");
  // Until the lists first arrive the page says it is loading: an empty list drawn before then read as an empty inbox
  const [isListsLoaded, setIsListsLoaded] = useState(false);
  const nextToastId = useRef(0);
  // The new path of a video just renamed on its page, kept open until the inbox lists it there
  const renamedVideo = useRef("");
  // What a new video's page is drawn afresh for: the video opened, never its new path after a rename, so the choices
  // made on the page stay
  const videoPageKey = useRef("");
  const openVideo = (item: InboxItem) => {
    videoPageKey.current = item.video;
    setOpened({ kind: "video", item });
  };

  const dismiss = useCallback((id: number) => setToasts((previous) => previous.filter((toast) => toast.id !== id)), []);

  // Settings typed but not saved, kept here so the top bar's button can save them from any view
  const [settingsProfile, setSettingsProfile] = useState("");
  // Which page the gear opens: the settings, or the workers' instructions
  const [settingsPage, setSettingsPage] = usePersistentState<SettingsPage>("settingsPage", "settings",
    (value) => SETTINGS_PAGES.some((page) => page.key === value));
  const [settingsDrafts, setSettingsDrafts] = useState<SettingDrafts>({});
  const [settingsVersion, setSettingsVersion] = useState(0);
  const [isSavingSettings, setIsSavingSettings] = useState(false);
  const draftCount = Object.keys(settingsDrafts).length;
  const tabBeforeSettings = useRef<Tab>("inbox");
  const setDraft = useCallback((key: string, draft: SettingDraft | null) => setSettingsDrafts((current) => {
    const next = { ...current };
    if (draft) next[key] = draft;
    else delete next[key];
    return next;
  }), []);
  const addMessage = useCallback((text: string, isError = false) => {
    const id = nextToastId.current++;
    // a message said again replaces the one shown; at most four, the oldest that closes by itself going first, unless
    // every one shown is an error, which stays until its × is pressed
    setToasts((previous) => {
      const kept = previous.filter((toast) => toast.text !== text);
      const others = kept.filter((toast) => !toast.isError);
      const dropped = kept.length >= 4 && others.length ? others[0].id : null;
      return [...kept.filter((toast) => toast.id !== dropped), { id, text, isError }];
    });
    if (!isError) window.setTimeout(() => dismiss(id), TOAST_MS);
  }, [dismiss]);

  // The phase list never changes while the server runs, so it is asked for until it arrives once: asked only at load,
  // a page opened while the server restarted kept an empty list, and every run's reset offered phase 0 alone
  const isPhasesLoaded = useRef(false);
  // Reads the lists again; the answer is the run list read, or null when the server did not answer
  const reloadLists = useCallback(async (): Promise<RunSummary[] | null> => {
    try {
      const [nextInbox, nextRuns, nextJobs] = await Promise.all([
        fetchJson<InboxItem[]>("/inbox"),
        fetchJson<RunSummary[]>("/runs"),
        fetchJson<Job[]>("/jobs"),
      ]);
      if (!isPhasesLoaded.current) {
        setPhases(await fetchJson<Phase[]>("/phases"));
        isPhasesLoaded.current = true;
      }
      setInbox(nextInbox);
      setRuns(nextRuns);
      setJobs(nextJobs);
      // an older server has no pause: the queue then reads as not paused
      fetchJson<QueuePause>("/queue/pause").then(setQueuePause).catch(() => setQueuePause(NO_QUEUE_PAUSE));
      setIsJobsLoaded(true);
      // the projects are no list the page needs to work: an older server has none
      fetchProjects().then((state) => setProjects(state.projects)).catch(() => setProjects([]));
      setIsListsLoaded(true);
      setIsServerDown(false);
      return nextRuns;
    } catch (error) {
      // the top bar says the server is not answering, and its title says what failed: nothing answered, or a list
      // the server (or the proxy in front of it) refused
      setIsServerDown(true);
      setListsError(describeError(error));
      return null;
    }
  }, []);

  // What a run needs, checked once when the page starts and again from the wizard. With no model configured the
  // wizard opens by itself: no run could start
  const [setupCheck, setSetupCheck] = useState<SetupCheck | null>(null);
  // In a container (`I2K_CONTAINER`) the machine is set up already: there is no wizard, and what a run lacks is a model
  // provider, named on the Settings page
  const isContainer = Boolean(setupCheck?.is_container);
  const missingProviders = setupCheck?.missing_providers ?? [];
  const isSetupNeeded = Boolean(setupCheck && (isContainer ? missingProviders.length > 0 : hasSetupProblem(setupCheck)));
  const [isSetupOpen, setIsSetupOpen] = useState(false);
  const loadSetup = useCallback(async () => {
    const check = await fetchJson<SetupCheck>("/setup");
    setSetupCheck(check);
    return check;
  }, []);
  // The check asked for (the wizard's Check again, Settings' Setup wizard): a check that cannot be read says why
  const checkSetup = useCallback(async (): Promise<SetupCheck | null> => {
    try {
      return await loadSetup();
    } catch (error) {
      addMessage(`The setup check could not be read: ${describeError(error)}`, true);
      return null;
    }
  }, [loadSetup, addMessage]);
  useEffect(() => {
    void loadSetup().then((check) => { if (!check.is_container && !check.has_models) setIsSetupOpen(true); })
      // at load a failed check is not announced: the top bar says when the server is down, and Check again says why
      .catch(() => undefined);
  }, [loadSetup]);
  // In a container the providers are set in Settings: the missing ones are read again after each save
  useEffect(() => {
    if (settingsVersion > 0 && isContainer) void loadSetup().catch(() => undefined);
  }, [settingsVersion, isContainer, loadSetup]);

  useEffect(() => {
    reloadLists();
    // The tag suggestions are gathered while the page loads, so a tag box opened later has them at once
    // a failed read is asked again by the first tag box opened, which says so if it fails again
    loadKnownTags().catch(() => undefined);
    const timer = window.setInterval(reloadLists, LIST_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [reloadLists]);

  // Opens a run folder by its path: from the run list when it is there, else from the list read again (a folder just
  // made by a branch or a conversion, one the 3-second poll has not shown yet). One the server does not list is said
  // so, and nothing opens later by surprise.
  const openRun = useCallback(async (root: string, name: string) => {
    const isIt = (run: RunSummary) => run.root === root && run.name === name;
    const run = runs.find(isIt) ?? (await reloadLists())?.find(isIt);
    if (run) setOpened({ kind: "run", run });
    else addMessage(`${root}/${name} is not in the run list`, true);
  }, [runs, reloadLists, addMessage]);

  // A run folder can move while it is open (from knowledge/ to output/, say). Its old address then answers nothing and
  // the view would freeze on its last state, so the view follows the folder of the same name, or closes.
  useEffect(() => {
    if (opened?.kind !== "run" || !isListsLoaded) return;
    const isStillThere = runs.some((run) => run.root === opened.run.root && run.name === opened.run.name);
    if (isStillThere) return;
    const moved = runs.find((run) => run.name === opened.run.name);
    if (moved) {
      setOpened({ kind: "run", run: moved });
      addMessage(`${moved.name} is now in ${moved.root}/; showing it there`);
    } else {
      setOpened(null);
      addMessage(`${opened.run.root}/${opened.run.name} no longer exists`, true);
    }
  }, [runs, opened, isListsLoaded, addMessage]);

  // A new video's page follows the video: its run in the detail view in its place once a run used it, its new state as the inbox
  // lists it, or closed when the inbox no longer holds it.
  useEffect(() => {
    if (opened?.kind !== "video" || isServerDown) return;
    const item = inbox.find((candidate) => candidate.video === opened.item.video);
    const runSummary = item?.runs.length ? runs.find((run) => run.root === item.runs[0].root && run.name === item.runs[0].name) : undefined;
    // A video renamed on its page is not in the inbox under its new path until the inbox is read again: kept open till then
    if (item && renamedVideo.current === item.video) renamedVideo.current = "";
    if (runSummary) setOpened({ kind: "run", run: runSummary });
    else if (!item && renamedVideo.current === opened.item.video) return;
    else if (!item) setOpened(null);
    else if (item !== opened.item) setOpened({ kind: "video", item });
  }, [inbox, runs, opened, isServerDown]);

  // The first time the lists arrive, the saved view is reopened, or forgotten when what it named is gone
  useEffect(() => {
    const saved = pendingOpen.current;
    if (saved === undefined || !isListsLoaded) return;
    pendingOpen.current = undefined;
    const found = saved === null ? null : findSavedOpen(saved, { runs, inbox, jobs });
    if (found) setOpened(found);
    else saveViewValue("opened", null);
  }, [runs, jobs, inbox, isListsLoaded]);

  useEffect(() => {
    if (pendingOpen.current !== undefined) return;
    saveViewValue("opened", toSavedOpen(opened));
  }, [opened]);

  const openKey = opened?.kind === "run" ? buildRunPath(opened.run) : "";
  const openJobId = opened?.kind === "job" ? opened.job.job_id : "";
  const queuedCount = jobs.filter((job) => job.status === "queued").length;
  // the queue's pause is offered once two jobs or more are in it (one running and the next waiting): stopping the
  // running one then starts the next at once, unless the queue is paused
  const isQueuePaused = queuePause.is_until_resumed || Boolean(queuePause.until);
  const pendingCount = jobs.filter((job) => ["queued", "retrying", "running", "blocked"].includes(job.status)).length;
  // "resume" ends the pause, "hold" pauses until resumed, a number pauses that many minutes; `isStopping` stops every
  // running run at once, as Ctrl+C does, and each goes on first, where it stopped, once the pause ends
  const changeQueuePause = async (choice: number | "resume" | "hold", isStopping = false) => {
    try {
      const body = { ...(typeof choice === "number" ? { minutes: choice } : {}),
        ...(isStopping ? { is_stopping_now: true } : {}) };
      const pause = await sendJson<QueuePause>(choice === "resume" ? "/queue/resume" : "/queue/pause",
        choice === "resume" ? {} : body);
      setQueuePause(pause);
      addMessage(isStopping ? "Every running run is stopped: they go on when you resume the queue"
        : pause.is_until_resumed ? "Queue paused until you resume it"
        : pause.until ? `Queue paused: no job starts before ${pause.until.slice(11, 16)}` : "Queue resumed");
    } catch (error) {
      addMessage(`The queue's pause could not be changed: ${describeError(error)}`, true);
    }
  };
  // the queue paused and every running run stopped at once, as Ctrl+C; each goes on first once the queue resumes
  const pauseNowItem = { label: "Pause now", icon: "stop", isDanger: true,
    hint: "each running run stops now, as Ctrl+C does, and goes on first, where it stopped, when you resume the queue",
    onSelect: () => void changeQueuePause("hold", true) };
  // What runs now is read from the run folders alone (a run started by hand has no job), the same folders the
  // running count's button opens
  const liveRuns = runs.filter((run) => run.is_live);
  const liveRun = liveRuns[0];
  const isSettingsOpen = tab === "settings";
  // Settings close back to the tab they were opened from, however they are left
  const closeSettings = () => setTab(tabBeforeSettings.current);
  // A panel's Edit instruction (the Change this document menu) opens that file in the Instructions editor; the editor
  // reads the file to open from the view state when it mounts. The instructions are an admin's alone.
  useEffect(() => {
    const open = (event: Event) => {
      if (!canAdmin) {
        addMessage("Only an admin can edit the instructions.", true);
        return;
      }
      const target = (event as CustomEvent<InstructionTarget>).detail;
      saveViewValue("instructionsWorkflow", target.workflow);
      saveViewValue("instructionsVariant", "");
      saveViewValue("instructionsFile", target);
      setSettingsPage("instructions");
      if (tab !== "settings") tabBeforeSettings.current = tab;
      setTab("settings");
    };
    window.addEventListener(OPEN_INSTRUCTION_EVENT, open);
    return () => window.removeEventListener(OPEN_INSTRUCTION_EVENT, open);
  }, [canAdmin, tab, setTab, setSettingsPage, addMessage]);
  // the run an open job's console writes, once the run list holds it
  const jobRun = opened?.kind === "job" ? runs.find((candidate) => candidate.folder === opened.job.request.output_root) : undefined;
  // The lists as the chosen project shows them: a run by its project, an inbox item by its run's (an item never run is
  // in the default project, where a new run starts)
  const projectOfRun = new Map(runs.map((run) => [run.name, run.project ?? DEFAULT_PROJECT]));
  const isShown = (projectSlug: string) => project === ALL_PROJECTS || projectSlug === project;
  const shownRuns = runs.filter((run) => isShown(run.project ?? DEFAULT_PROJECT));
  const shownInbox = inbox.filter((item) => isShown(projectOfRun.get(item.run_folder.split("/").pop() ?? "") ?? DEFAULT_PROJECT));
  // a project chosen earlier that the account may no longer see shows every project again
  useEffect(() => {
    if (projects.length && project !== ALL_PROJECTS && !projects.some((item) => item.slug === project)) setProject(ALL_PROJECTS);
  }, [projects, project, setProject]);
  const projectTitle = project === ALL_PROJECTS ? "All projects"
    : projects.find((item) => item.slug === project)?.title ?? project;
  const tabs: { key: Tab; label: string; icon: string; count: number }[] = [
    { key: "inbox", label: "Inbox", icon: "inbox", count: shownInbox.filter((item) => item.state === "new").length },
    { key: "queue", label: "Queue", icon: "list", count: queuedCount },
    { key: "manager", label: "Manager", icon: "layers", count: 0 },
  ];

  // A Manager tool's view; a tool added to MANAGER_TOOLS without a view here fails the type check
  const renderTool = (tool: ManagerTool) => {
    switch (tool) {
      case "download": return <DownloadView runs={shownRuns} onBack={() => setOpened(null)} onMessage={addMessage} />;
      case "rename": return <RenameView runs={shownRuns} onBack={() => setOpened(null)} onChanged={reloadLists} onMessage={addMessage} />;
      case "compress": return <CompressView onBack={() => setOpened(null)} onMessage={addMessage} />;
      case "users": return <AccountsView onBack={() => setOpened(null)} onMessage={addMessage} />;
      case "groups": return <GroupsView onBack={() => setOpened(null)} onMessage={addMessage} />;
      case "projects": return <ProjectsView onBack={() => setOpened(null)} onMessage={addMessage} />;
      default: {
        const unknownTool: never = tool;
        return unknownTool;
      }
    }
  };

  return (
    <div className={`shell${opened ? " has-open" : ""}${isSettingsOpen ? " shows-settings" : ""}`}>
      <header className="topbar">
        <div className="brand">
          <span className="logo">i2k</span>
          <span className="brand-name">info-to-knowledge</span>
        {/* the project the lists show, beside the app's name: what the whole page is looking at */}
        {projects.length > 1 && (
          <Dropdown label={projectTitle} icon="archive" title="the project the lists show" popup="menu">
            {(close) => (
              <div role="group" aria-label="projects" className="menu project-menu">
                {[{ slug: ALL_PROJECTS, title: "All projects" }, ...projects].map((item) => (
                  <button key={item.slug} type="button" role="menuitemradio" aria-checked={project === item.slug}
                    className={project === item.slug ? "on" : ""}
                    onClick={() => { setProject(item.slug); close(); }}>{item.title}</button>
                ))}
              </div>
            )}
          </Dropdown>
        )}
        </div>
        <div className="top-stats">
          {isServerDown ? (
            <span className="pill failed" title={listsError}>server not answering</span>
          ) : (
            <>
              {/* the running count is a menu: each running run by name with its phase and provider set, so the owner
                  picks the run (it once opened whichever came first), then Pause all and Stop all for every run at
                  once (the owner: "buttons in the UI: kill all or stop all, pause all") */}
              {liveRun ? (
                <Menu label={<><span className="dot running" /> {liveRuns.length} running</>}
                  variant="ghost stat stat-link" title="open a running run, or pause or stop them all"
                  items={[
                    ...liveRuns.map((run) => ({
                      label: run.display_name || run.name, icon: "eye", group: "Open a run",
                      hint: [run.live_phase ? `phase ${run.live_phase}` : "", `provider set ${run.provider_set || "default"}`]
                        .filter(Boolean).join(" · "),
                      onSelect: () => {
                        if (isSettingsOpen) closeSettings();
                        setOpened({ kind: "run", run });
                      },
                    })),
                    ...(isQueuePaused ? [] : [{ label: "Pause all", icon: "pause", group: "Every run",
                      hint: "each run waits at its next step, and no waiting job starts, until you resume the queue",
                      onSelect: () => void changeQueuePause("hold") }]),
                    { ...pauseNowItem, label: "Pause all now", group: "Every run" },
                  ]} />
              ) : (
                <span className="stat"><span className="dot" /> 0 running</span>
              )}
              {/* the queued count is the queue's pause button too, once there is a queue to pause (the owner: one
                  control, not two) */}
              {isQueuePaused ? (
                <Menu label={`${queuedCount} queued · ${queuePause.is_until_resumed ? "paused"
                    : `paused until ${queuePause.until.slice(11, 16)}`}`}
                  icon="pause" variant="ghost stat queue-pause on" title="the queue is paused"
                  note={QUEUE_PAUSE_NOTE}
                  items={[
                    { label: "Resume now", icon: "play", hint: "the paused run goes on, then the waiting jobs",
                      onSelect: () => void changeQueuePause("resume") },
                    ...(queuePause.is_until_resumed ? [] : [{ label: "Pause until I resume", icon: "pause",
                      onSelect: () => void changeQueuePause("hold") }]),
                    ...(liveRun ? [pauseNowItem] : []),
                    ...QUEUE_PAUSE_MINUTES.map((minutes) => ({ label: `Pause ${describePauseLength(minutes)} from now`, icon: "pause",
                      onSelect: () => void changeQueuePause(minutes) })),
                  ]} />
              ) : pendingCount > 1 ? (
                <Menu label={<><span className={`dot ${queuedCount ? "queued" : ""}`} /> {queuedCount} queued</>}
                  variant="ghost stat queue-pause" title="pause the queue" note={QUEUE_PAUSE_NOTE}
                  items={[
                    { label: "Pause until I resume", icon: "pause", onSelect: () => void changeQueuePause("hold") },
                    // the queue's own menu stops the running runs too (the owner: "I already have a global menu that
                    // can be enhanced")
                    ...(liveRun ? [pauseNowItem] : []),
                    ...QUEUE_PAUSE_MINUTES.map((minutes) => ({ label: `Pause ${describePauseLength(minutes)}`, icon: "pause",
                      onSelect: () => void changeQueuePause(minutes) })),
                  ]} />
              ) : (
                <span className="stat"><span className={`dot ${queuedCount ? "queued" : ""}`} /> {queuedCount} queued</span>
              )}
            </>
          )}
          {/* one who may open the settings sees it as a mark on the gear (the owner: the button beside the gear "look very
              ugly"); anyone else keeps the button */}
          {isSetupNeeded && !canAdmin && (
            <button className="btn ghost setup-button" title="Something a run needs is not set up"
              onClick={() => isContainer
                ? addMessage(`A run needs a model provider that is not set up: ${describeMissingProviders(missingProviders)}`, true)
                : setIsSetupOpen(true)}>
              <Icon name="alert" /> {isContainer ? "Provider missing" : "Setup needed"}
            </button>
          )}
          {draftCount > 0 ? (
            <button className="btn primary settings-button" disabled={isSavingSettings} onClick={async () => {
              setIsSavingSettings(true);
              const outcome = await saveDrafts(settingsDrafts, settingsProfile);
              setSettingsDrafts(outcome.left);
              setSettingsVersion((version) => version + 1);
              setIsSavingSettings(false);
              addMessage(outcome.message, outcome.isError);
            }}>
              {isSavingSettings ? <><span className="spinner" aria-hidden="true" /> Saving…</>
                : <><Icon name="settings" /> Save settings ({draftCount})</>}
            </button>
          ) : null}
          {draftCount > 0 && isSettingsOpen && (
            <button className="btn ghost" disabled={isSavingSettings} title="drop every change not saved yet"
              onClick={() => setSettingsDrafts({})}>
              Discard changes
            </button>
          )}
          {account && (
            <span className="stat account-pill" title={`signed in as ${account.name}, ${account.role}`}>
              <Icon name="lock" size={14} /> {account.name} · {account.role}
              <button className="btn ghost" onClick={onSignOut}>Sign out</button>
            </span>
          )}
          {canAdmin && (
            // Closed: the gear alone. Open: how to leave, in words; changes not saved stay marked for when it opens again
            <button className={`btn ghost settings-button${isSettingsOpen ? " on" : " icon-only"}${isSetupNeeded ? " has-alert" : ""}`}
              aria-label={isSettingsOpen ? "Close settings" : isSetupNeeded ? (isContainer ? "Settings: provider missing" : "Settings: setup needed") : "Settings"}
              title={isSettingsOpen ? "close the settings"
                : isSetupNeeded ? (isContainer ? "Settings: a model provider a run needs is missing"
                  : "Settings: something a run needs is not set up") : "Settings"}
              onClick={() => {
                if (isSettingsOpen) {
                  closeSettings();
                  return;
                }
                tabBeforeSettings.current = tab;
                setTab("settings");
              }}>
              {isSettingsOpen ? <><Icon name="x" size={14} /> Close<span className="close-more"> settings</span></> : <Icon name="settings" size={14} />}
              {isSetupNeeded && !isSettingsOpen && <span className="alert-mark" aria-hidden="true">!</span>}
            </button>
          )}
        </div>
      </header>

      {/* Settings is a page of its own: the sidebar (Inbox, Queue) showed empty beside it, so it goes while they are
          open and the settings take the width; Close settings in the top bar leads back */}
      <div className={`layout${isSettingsOpen ? " without-sidebar" : ""}`}>
        {!isSettingsOpen && <aside className="sidebar">
          <nav className="tabs" role="tablist">
            {tabs.map((item) => (
              <button key={item.key} role="tab" aria-selected={tab === item.key} className={tab === item.key ? "on" : ""}
                onClick={() => {
                setTab(item.key);
                // On a phone the open run covers the lists: a tab leads back to its list
                if (isPhoneWidth()) setOpened(null);
              }}>
                <Icon name={item.icon} /> {item.label}
                {item.count > 0 && <span className="count">{item.count}</span>}
              </button>
            ))}
          </nav>
          {tab === "inbox" && (
            <InboxPanel items={shownInbox} isLoading={!isListsLoaded && !isServerDown} phases={phases} runs={shownRuns} jobs={jobs} openKey={openKey}
              onOpenJob={(job) => setOpened({ kind: "job", job })} onChanged={reloadLists} onMessage={addMessage}
              onOpenVideo={openVideo}
              onOpenRun={(root, name) => void openRun(root, name)} />
          )}
          {tab === "queue" && (
            <QueuePanel jobs={jobs} isLoaded={isJobsLoaded} openJobId={openJobId} onOpen={(job) => setOpened({ kind: "job", job })}
              onChanged={reloadLists} onMessage={addMessage} />
          )}
          {tab === "manager" && (
            // the users, groups and projects stand under the run tools, an admin's alone
            <ManagerPanel openTool={opened?.kind === "tool" ? opened.tool : null} isAdminShown={canAdmin}
              onOpenTool={(tool) => setOpened({ kind: "tool", tool })} />
          )}
        </aside>}

        <main className="main">
          {isSettingsOpen && (
            <div className="settings-pages" role="tablist" aria-label="settings or instructions">
              {SETTINGS_PAGES.map((page) => (
                <button key={page.key} type="button" role="tab" aria-selected={settingsPage === page.key}
                  className={settingsPage === page.key ? "on" : ""} onClick={() => setSettingsPage(page.key)}>
                  {page.label}
                </button>
              ))}
            </div>
          )}
          {isSettingsOpen && settingsPage === "instructions" && <InstructionsView onMessage={addMessage} />}
          {isSettingsOpen && settingsPage === "settings" && (
            <SettingsView profile={settingsProfile} drafts={settingsDrafts} onDraft={setDraft} version={settingsVersion}
              onOpenSetup={isContainer ? undefined : () => void checkSetup().then((check) => { if (check) setIsSetupOpen(true); })}
              isSetupNeeded={isSetupNeeded} missingProviders={isContainer ? missingProviders : []}
              onMessage={addMessage} onProfileChange={(profile) => {
                // A draft belongs to the files it was typed for
                setSettingsDrafts({});
                setSettingsProfile(profile);
              }} />
          )}
          {!isSettingsOpen && <>
            {opened?.kind === "run" && (
              <RunDetail key={openKey} run={opened.run} phases={phases} jobs={jobs} onBack={() => setOpened(null)}
                onChanged={reloadLists} onMessage={addMessage}
                onOpenRun={(root, name) => void openRun(root, name)} />
            )}
            {opened?.kind === "tool" && renderTool(opened.tool)}
            {opened?.kind === "video" && (
              <VideoDetail key={videoPageKey.current} item={opened.item} phases={phases} jobs={jobs} onBack={() => setOpened(null)}
                onOpenJob={(job) => setOpened({ kind: "job", job })} onChanged={reloadLists} onMessage={addMessage}
                onRenamed={(item) => { renamedVideo.current = item.video; setOpened({ kind: "video", item }); }} />
            )}
            {opened?.kind === "job" && (
              <JobConsole key={openJobId} job={opened.job} onBack={() => setOpened(null)} onChanged={reloadLists}
                onMessage={addMessage} onOpenRun={jobRun ? () => setOpened({ kind: "run", run: jobRun }) : undefined} />
            )}
            {!opened && !isListsLoaded && !isServerDown && (
              <div className="placeholder"><Spinner /></div>
            )}
            {!opened && (isListsLoaded || isServerDown) && (
              <div className="placeholder">
                <Icon name="layers" size={32} />
                <h2>Nothing open</h2>
                <p className="muted">Open a run to follow its phases, its log and what it produced, or a job to read what it printed.</p>
              </div>
            )}
          </>}
        </main>
      </div>

      {isSetupOpen && setupCheck && !setupCheck.is_container && (
        <SetupWizard check={setupCheck} onClose={() => setIsSetupOpen(false)} onChanged={checkSetup}
          onMessage={addMessage} />
      )}
      <Toasts toasts={toasts} onDismiss={dismiss} />
    </div>
  );
}
