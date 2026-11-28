import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { buildDownloadUrl, buildVersionsDownloadUrl, buildRunBase, saveRunDisplayName, buildRunPath, describeDocumentPass, describeError, describePdfChoice, describePdfMode, formatDateTime, formatDuration,
  formatPhaseTitle, formatRunState, formatSize, isSameName, NO_SELECTION, PDF_MODES, queueJobs, saveRunTags, sendFile, sendJson } from "../api";
import type { CommandOutput, Job, Phase, PhaseSelection, RecordingCompression, RunDetail as Detail, RunSignal, RunSummary } from "../api";
import { sendJobAction } from "../jobs";
import { useUpdateStream } from "../useUpdateStream";
import { readViewState, readViewValue, saveViewValue, usePersistentState } from "../viewState";
import AnotherPartDialog from "./AnotherPartDialog";
import type { RangeValue } from "./RangeEditor";
import ArtifactsView from "./ArtifactsView";
import BatchHistory from "./BatchHistory";
import Dialog from "./Dialog";
import DocumentSteps from "./DocumentSteps";
import { Dropdown, Menu, PanelNote } from "./Dropdown";
import Icon from "./Icon";
import JobFailureControls, { useHoldQueueItem } from "./JobFailureControls";
import RunTitle from "./RunTitle";
import LogView from "./LogView";
import PhaseSelector from "./PhaseSelector";
import RecordingView from "./RecordingView";
import ResetDialog from "./ResetDialog";
import type { ResetKind } from "./ResetDialog";
import { DocumentPassPanel, isProfileGone, useIngestingProfiles, usePhaseModels, useRunDefaults, useSettingsProfiles }
  from "./RunChoices";
import { ProviderSetChoice, nameProviderSet, useProviderSets } from "./ProviderSetChoice";
import WorkflowSpeedChoice, { describeSpeed } from "./WorkflowSpeedChoice";
import { nameSpeedPreset } from "../speed";
import { chooseProgress, describeUnit, listStages } from "./runProgress";
import PassPartsTable from "./PassPartsTable";
import WorkerLive from "./WorkerLive";
import type { WorkerSession } from "./WorkerSessions";
import { RunTags } from "./RunTags";
import StageTable from "./StageTable";
import WorkerView from "./WorkerView";

// What a phase does, behind a small "?" on its card.
function PhaseHelp({ phase }: { phase?: Phase }) {
  if (!phase?.summary) return null;
  return (
    <Dropdown icon="help" variant="ghost icon-only tiny" title={`what phase ${phase.number} does`}>
      {() => (
        <div className="phase-help">
          <strong>Phase {phase.number} · {formatPhaseTitle(phase)}</strong>
          <p>{phase.summary}</p>
          <dl>
            <dt>Reads</dt><dd>{phase.reads}</dd>
            <dt>Writes</dt><dd>{phase.writes}</dd>
            <dt>Model</dt><dd>{phase.is_model_used ? "yes, it calls the model" : "no, plain code"}</dd>
          </dl>
        </div>
      )}
    </Dropdown>
  );
}

// What each section of the Copy and run again menu does, behind the "?" of its top line (the owner)
const COPY_GROUP_HELP: Record<string, string> = {
  "Another profile": "writes the document again in another profile (presentation, meeting, …): phases 4 and 5 run again",
  "Another processing or document": "reads the PDF again as text or as page pictures, and writes its document as "
    + "markdown or as knowledge: phase 2 and every later phase run again",
  "Another part": "processes another part of the input, other times of the recording or other pages of the PDF: every "
    + "phase runs again, queued at once",
  "From a phase": "removes the phase you choose and every later one in the copy; press Resume there to run them",
};
function CopyMenuHelp({ groups }: { groups: string[] }) {
  return (
    <>
      <p>The frames and the recording are linked, not copied, so a copy takes little disk space.</p>
      <dl>{groups.map((group) => <Fragment key={group}><dt>{group}</dt><dd>{COPY_GROUP_HELP[group]}</dd></Fragment>)}</dl>
    </>
  );
}

// The title of a progress card that opens its table of details (a phase's stages, batches or steps)
function DetailsToggle({ isOpen, onToggle, noun, title, children }: {
  isOpen: boolean;
  onToggle: () => void;
  noun: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <button className="btn ghost small frames-toggle" aria-expanded={isOpen} aria-label={`${isOpen ? "hide" : "show"} ${noun} details`}
      title={title} onClick={onToggle}>
      <Icon name="chevron" size={14} /> <strong>{children}</strong>
    </button>
  );
}

interface RunUpdate {
  run: Detail;
  offset: number;
}

const VIEWS = ["progress", "artifacts", "recording"] as const;
type View = (typeof VIEWS)[number];

// A recording action (move either way, delete, upload) is shown working from the click until the page sees its result
// in the run's state (it comes a second later with the run's updates), or until it fails.
type RecordingAction = "to-inbox" | "to-run" | "delete" | "upload";
// a PDF is no recording to the reader: every line about it names it "PDF"
const describeRecordingWork = (action: RecordingAction, source: string): string => ({
  "to-inbox": `Moving ${source} to inbox…`, "to-run": `Moving ${source} to run folder…`,
  delete: `Moving ${source} to trash…`, upload: `Uploading ${source}…` })[action];

// The reports put away (OK on the worker feedback card), by run folder: one cookie entry for every run, the newest kept
// when it grows, never one entry per run opened
// The phase a document pass (clean up, rewrite, knowledge) runs as
const DOCUMENT_PASS_PHASE = 7;
const FEEDBACK_SEEN_KEY = "feedbackSeen";
const FEEDBACK_SEEN_KEPT = 20;
// the older page's one entry per run, which filled the cookie past its size limit
const RETIRED_FEEDBACK_PREFIX = "feedbackSeen:";

// The kept recording's compression, or null when it was never compressed (the server sends an empty record then)
// A run made on either fast workflow, the earlier one or the direct draft, is a Fast run
function isFastWorkflow(workflow: string | undefined): boolean {
  return workflow === "fast-processing" || workflow === "direct-draft";
}

function readCompression(recorded: Detail["recording_compression"]): RecordingCompression | null {
  return recorded && "status" in recorded ? recorded as RecordingCompression : null;
}

// What a request waiting in the run's control file will do, in words
function describeSignal(signal: string, isHeld: boolean): string {
  if (signal === "pause" && isHeld) return "Paused: the run holds here until you press Continue.";
  if (signal === "pause") return "Pause requested: the model call running now finishes first, then the run holds before its next call, until you press Continue.";
  if (signal === "drain") return "Drain requested: the run finishes the item it is on, then stops. A resume goes on from there.";
  return "Stop requested: the run stops at its next check, before its next model call. Nothing written is lost; a resume goes on from there.";
}

// One run folder, live: its phases, its frames by status, the signal waiting for it, its actions and its newest log.
export default function RunDetail({
  run,
  phases,
  jobs,
  onBack,
  onChanged,
  onMessage,
  onOpenRun = () => undefined,
}: {
  run: RunSummary;
  phases: Phase[];
  jobs: Job[];
  onBack: () => void;
  onChanged: () => void;
  onMessage: (text: string, isError?: boolean) => void;
  // a run folder the page names (a copy it made, the run a branch came from); the app opens it or says it is not listed
  onOpenRun?: (root: string, name: string) => void;
}) {
  // 1. The run's state: the list's summary until the first update, then each update
  const base = buildRunBase(run);
  const { latest, text } = useUpdateStream<RunUpdate>(`${base}/update`);
  const detail = latest?.run;
  const summary: RunSummary = detail ?? run;
  // Every run queued from this page starts from the settings profile this run was made with, so a resume of a strict
  // run stays strict
  const [selection, setSelection] = useState<PhaseSelection>({ ...NO_SELECTION, profile: run.settings_profile ?? "" });
  const [reset, setReset] = useState<{ kind: ResetKind; number: number } | null>(null);
  // the Copy and run again menu's Another part, open in its dialog
  const [isPartOpen, setIsPartOpen] = useState(false);
  const [isRunOpen, setIsRunOpen] = useState(false);
  // the Unblock dialog: the provider set the unblock runs on is chosen there, and nothing is queued until its button
  const [isUnblockOpen, setIsUnblockOpen] = useState(false);
  const [unblockSet, setUnblockSet] = useState("");
  const providerSets = useProviderSets();
  // A file a card asked to be read, handed to the artifacts view once, so a notice opens what it is about.
  const [openPath, setOpenPath] = useState("");
  const [livePhase, setLivePhase] = useState<number | null>(null);
  // a fast run's phase 3 session opened from its row of the Frames table
  const [fastSession, setFastSession] = useState("");
  const [savedView, setView] = usePersistentState<View>("detailView", "progress", (value) => VIEWS.includes(value as View));
  const [seekTo, setSeekTo] = useState<number | null>(null);
  // The phase card clicked: the bar shows that phase's progress, a finished one's too; clicked again, the running one's
  const [chosenPhase, setChosenPhase] = useState<number | null>(null);
  const clearSeek = useCallback(() => setSeekTo(null), []);
  // the one switch that opens the table under a progress card: a phase's stages, batches or steps
  const [isDetailsTableOpen, setIsDetailsTableOpen] = usePersistentState("batchesOpen", false);
  // the phase whose worker's instructions and input are open, and the step it opens on
  const [workerPhase, setWorkerPhase] = useState<Phase | null>(null);
  const [workerStep, setWorkerStep] = useState("");
  const [recordingAction, setRecordingAction] = useState<RecordingAction | null>(null);
  const uploadInput = useRef<HTMLInputElement>(null);

  // 2. How many reports were on screen when OK was last pressed: the card stays away until a worker reports one more
  // (saved only when OK is pressed, so a run opened and left adds nothing to the cookie)
  const [feedbackSeen, setFeedbackSeen] = useState<Record<string, number>>(() => readViewValue(FEEDBACK_SEEN_KEY, {}));
  const hiddenFeedback = feedbackSeen[buildRunPath(run)] ?? 0;
  const hideFeedback = (reports: number) => {
    const others = Object.entries(readViewValue<Record<string, number>>(FEEDBACK_SEEN_KEY, {}))
      .filter(([path]) => path !== buildRunPath(run));
    const next = Object.fromEntries([...others.slice(-(FEEDBACK_SEEN_KEPT - 1)), [buildRunPath(run), reports]]);
    saveViewValue(FEEDBACK_SEEN_KEY, next);
    setFeedbackSeen(next);
  };
  useEffect(() => {
    Object.keys(readViewState()).filter((key) => key.startsWith(RETIRED_FEEDBACK_PREFIX)).forEach((key) => saveViewValue(key, null));
  }, []);

  // 3. The lists the menus offer: settings profiles (a run made with one whose file is gone resumes on the default
  // settings, since a run line naming a missing profile is refused), the models, and the ingesting profiles
  const settingsProfiles = useSettingsProfiles();
  const { phaseModels } = usePhaseModels();
  const ingestingProfiles = useIngestingProfiles();
  useEffect(() => {
    if (ingestingProfiles.error) onMessage(ingestingProfiles.error, true);
  }, [ingestingProfiles.error, onMessage]);
  const madeWithProfile = summary.settings_profile ?? "";
  const isMadeWithProfileGone = isProfileGone(madeWithProfile, settingsProfiles.profiles);
  // A video run's Workflow speed for its next resume or phase run: the one chosen here, else the run's own (a run made
  // on the Fast workflow before speeds were kept is Fast); an untouched run of the settings' speed sends none
  const [speedChoice, setSpeedChoice] = useState<Pick<PhaseSelection, "speed_preset" | "speed_settings"> | null>(null);
  const runDefaults = useRunDefaults(isMadeWithProfileGone ? "" : madeWithProfile);
  const keptSpeed = detail?.speed ? { speed_preset: detail.speed.preset, speed_settings: detail.speed.values }
    : isFastWorkflow(detail?.workflow) ? { speed_preset: "fast", speed_settings: {} } : {};
  const runSpeed = detail?.workflow ? speedChoice ?? keptSpeed : {};
  const runChoice: PhaseSelection = { ...NO_SELECTION, profile: isMadeWithProfileGone ? "" : madeWithProfile, ...runSpeed };
  useEffect(() => {
    if (isMadeWithProfileGone) setSelection((current) => current.profile === madeWithProfile ? { ...current, profile: "" } : current);
  }, [isMadeWithProfileGone, madeWithProfile]);

  // 4. What the folder is and can do next: a live run can only be controlled, a run waiting in the queue can only
  // wait, and a resume is offered only while a phase is not complete
  // the run record may name no video, topic or AI name where the list's copy does: an empty one falls back to it
  const video = detail?.video || run.video;
  const status = summary.status;
  const isLive = Boolean(detail?.live_pids.length);
  const pdfMode = summary.pdf_mode ?? "";
  // a PDF is no recording to the reader: its tab and menu are named "PDF"; the server sets its mode for a PDF alone
  const isPdf = Boolean(pdfMode);
  const unit = describeUnit(pdfMode);
  // A saved Recording tab falls back to Progress on a run whose recording is not there.
  const view: View = savedView === "recording" && detail && !detail.has_recording ? "progress" : savedView;
  const waitingJob = jobs.find((job) => job.request.output_root === run.folder && job.status === "queued");
  const runningJob = jobs.find((job) => job.request.output_root === run.folder && job.status === "running");
  // a job of this run that failed and waits for its retry, or holds the queue after its last try
  const retryingJob = jobs.find((job) => job.request.output_root === run.folder
    && (job.status === "retrying" || job.status === "blocked"));
  const openJob = runningJob ?? retryingJob ?? waitingJob;
  // the job's choice for after its last failed try: in the Control menu while the run is live, a menu of its own else
  const holdItem = useHoldQueueItem(openJob ?? null, onChanged, onMessage);
  const phaseRows = detail?.phases ?? [];
  // A phase switched off in the settings, or recorded "off" by the run that passed it, is grayed out and never open
  // Phase 7 is off in the settings unless a run asks for a pass: a run that asked for one has it on, so a pass stopped
  // mid-way is still open and offers Resume (the owner interrupted a knowledge pass and found no Resume button)
  const isPhaseOff = (phase: { number: number; status: string }) =>
    phase.status === "off" || (phase.status !== "complete" && phase.status !== "running"
      && !(phase.number === DOCUMENT_PASS_PHASE && summary.document_pass)
      && phases.find((known) => known.number === phase.number)?.is_on === false);
  // A phase has run when its record says so, or when time was spent in it: a run that died mid-phase leaves it "not run"
  const hasPhaseRun = (phase: { name: string; status: string }) => phase.status !== "not run"
    || (detail?.timing.phases[phase.name] ?? 0) > 0;
  const firstOpen = phaseRows.find((phase) => phase.status !== "complete" && !isPhaseOff(phase));
  const isAllComplete = phaseRows.length > 0 && !firstOpen;
  // a chapter can be taken back once phase 4 has started writing
  const documentPhase = phaseRows.find((phase) => phase.number === 4);
  const isDocumentStarted = (documentPhase !== undefined && hasPhaseRun(documentPhase))
    || (detail?.document_stages ?? []).some((stage) => (stage.counts.accepted ?? 0) > 0);
  const runningPhase = phaseRows.find((phase) => phase.status === "running");
  // A blocked step is unblocked by asking it again: a resume gives every step a fresh attempt and opens with the
  // rejection that blocked it. A blocked frame is asked again alone (`--worker N`).
  const blockedPhase = phaseRows.find((phase) => phase.status === "blocked" || phase.status === "failed");
  const blockedFrames = (detail?.blocked_items ?? [])
    .map((slug) => Number(slug.match(/(\d+)$/)?.[1]))
    .filter((number) => !Number.isNaN(number));
  const canStart = Boolean(detail) && !isLive && !waitingJob && !retryingJob;
  // Phases 1 and 2 read the recording. While a done run keeps it in its folder, or it is found nowhere, they cannot
  // run: their actions are not offered, the phase choice starts at phase 3 and "every phase" runs from there, and one
  // line says why, beside the button that moves a kept recording back to the inbox.
  const isRecordingKept = Boolean(detail?.is_recording_kept);
  const source = isPdf ? "PDF" : "recording";
  let lockReason = "";
  if (detail && isRecordingKept) lockReason = `Phases 1 and 2 read the ${source}, kept in this run folder: Move ${source} to inbox to run them.`;
  else if (detail && !detail.has_recording) lockReason = `Phases 1 and 2 read the ${source}, which is not in the inbox, the archive or a run folder.`;
  const isRecordingLocked = Boolean(lockReason);
  const firstRunnable = isRecordingLocked ? 3 : 1;
  // the run's own phases under the names its cards use (a PDF's "text reading"), never phases it does not have
  const runPhases: Phase[] = phaseRows.length ? phaseRows.filter((row) => !isPhaseOff(row)).map((row) => {
    const known = phases.find((candidate) => candidate.number === row.number);
    return known ? { ...known, ...row } : row as Phase;
  }) : phases;
  const runnablePhases = runPhases.filter((phase) => phase.number >= firstRunnable);
  // With the recording locked, a choice that would start before the first phase that can run starts there instead
  const fitSelection = (choice: PhaseSelection): PhaseSelection => {
    const startsBeforeRunnable = !choice.is_resuming && !choice.worker_indexes.length
      && (choice.only_phase ?? choice.from_phase ?? 1) < firstRunnable;
    return isRecordingLocked && startsBeforeRunnable ? { ...choice, only_phase: null, from_phase: firstRunnable } : choice;
  };

  // 5. The actions; a refusal names the run and what was not done
  const reportFailure = (what: string, error: unknown) => onMessage(`${run.name}: ${what}: ${describeError(error)}`, true);

  // A copy of this run in a new run folder beside it (another profile, a PDF read again, a branch): the server names
  // the folder it made, the message says it, and the page opens it, which reads the lists again
  const copyRun = async (route: string, body: object, describe: (folder: string, answer: CommandOutput & { jobs?: Job[] }) => string) => {
    try {
      const answer = await sendJson<CommandOutput & { jobs?: Job[]; records?: { name?: string; folder?: string }[] }>(`${base}/${route}`, body);
      // a branch's and a part's record name the folder; a conversion's gives its path alone
      const record = answer.records?.[0];
      const folder = record?.name ?? record?.folder?.split("/").pop();
      if (answer.exit_code !== 0 || !folder) {
        onMessage(answer.output, true);
        onChanged();
        return;
      }
      onMessage(describe(folder, answer), false);
      onOpenRun(run.root, folder);
    } catch (error) {
      reportFailure("no copy was made", error);
    }
  };
  // A finished run written again in another ingesting profile: phase 4 queued there
  const convert = (profile: string) => copyRun("convert", { profile },
    (folder, answer) => `${run.name} copied to ${folder}; phase 4 queued (job ${answer.jobs?.[0]?.job_id})`);
  // A finished audio run's document written again in another profile in this run: the document it has stays in the
  // Open document menu, and phase 4 is queued here
  const writeInPlace = async (profile: string) => {
    try {
      const answer = await sendJson<CommandOutput & { jobs?: Job[] }>(`${base}/convert`, { profile, is_in_place: true });
      onMessage(answer.exit_code === 0 ? `${run.name} is written again as ${profile}; the document it had stays in `
        + `Open document (job ${answer.jobs?.[0]?.job_id})` : answer.output, answer.exit_code !== 0);
      onChanged();
    } catch (error) {
      reportFailure(`${run.name} was not written again`, error);
    }
  };
  // Another part of the input: copied into a new run folder from phase 1 with that part, queued
  const runPart = (value: RangeValue) => {
    setIsPartOpen(false);
    void copyRun("part", value, (folder, answer) => `${run.name} copied to ${folder} for the part you chose; phases 1 `
      + `on queued (job ${answer.jobs?.[0]?.job_id})`);
  };
  // The phases before `phase` kept, optionally in the PDF's other processing mode; Resume runs it
  const branch = (phase: number, newPdfMode = "") => copyRun("branch", newPdfMode ? { phase, pdf_mode: newPdfMode } : { phase },
    (folder) => `${run.name} branched from phase ${phase} into ${folder}; press Resume there to run it`);

  const queue = async (choice: PhaseSelection, what: string) => {
    if (!video) {
      onMessage(`${run.name} has no run record naming its video; process it from the inbox.`, true);
      return;
    }
    try {
      const [queued] = await queueJobs([{ video, output: run.folder }], choice);
      onMessage(`Queued ${what} of ${run.name} (job ${queued.job_id})`);
      onChanged();
    } catch (error) {
      reportFailure(`${what} was not queued`, error);
    }
  };
  const actOnJob = async (job: Job, action: "cancel" | "interrupt") => {
    try {
      onMessage(await sendJobAction(job, action));
      onChanged();
    } catch (error) {
      reportFailure(action === "cancel" ? "the job was not taken off the queue" : "the run was not interrupted", error);
    }
  };
  const signal = async (name: RunSignal) => {
    try {
      const answer = await sendJson<{ signal: RunSignal | null }>(`${base}/control`, { signal: name });
      onMessage(answer.signal ? `${run.name}: ${answer.signal} requested; the run acts on it at its next check`
        : `${run.name}: request taken back; the run goes on`);
    } catch (error) {
      reportFailure(`${name} was not sent`, error);
    }
  };

  // 6. The recording kept with the run: moved either way, put in the trash, uploaded, or compressed
  useEffect(() => {
    if (!detail || !recordingAction) return;
    const isSeen = { "to-inbox": !detail.is_recording_kept, "to-run": detail.is_recording_kept,
      delete: !detail.has_recording, upload: detail.has_recording }[recordingAction];
    if (isSeen) setRecordingAction(null);
  }, [recordingAction, detail]);
  const runRecordingAction = async <T,>(action: RecordingAction, send: () => Promise<T>, describe: (answer: T) => string,
                                        isFailed: (answer: T) => boolean = () => false) => {
    setRecordingAction(action);
    try {
      const answer = await send();
      onMessage(describe(answer), isFailed(answer));
      if (isFailed(answer)) setRecordingAction(null);
      onChanged();
    } catch (error) {
      setRecordingAction(null);
      reportFailure(describeRecordingWork(action, source).replace(/…$/, "").toLowerCase() + " failed", error);
    }
  };
  const moveRecording = (place: "to-inbox" | "to-run") => runRecordingAction(place,
    () => sendJson<CommandOutput>(`${base}/recording/${place}`),
    (answer) => answer.output.trim() || `exit code ${answer.exit_code}`, (answer) => answer.exit_code !== 0);
  const deleteRecording = () => runRecordingAction("delete",
    () => sendJson<{ moved_as: string }>(`${base}/recording/delete`),
    (answer) => `${run.name}: ${source} moved to the inbox trash as ${answer.moved_as}; Restore there brings it back`);
  const uploadRecording = (file: File) => runRecordingAction("upload",
    () => sendFile<{ saved_as: string }>(`${base}/recording/upload`, file),
    (answer) => `${run.name}: ${source} saved as ${answer.saved_as}`);
  // Compressing runs for minutes in a process of its own; its state comes with the run's state, not from the click. A
  // recording never compressed has an empty record.
  const compression = readCompression(detail?.recording_compression);
  const isCompressing = compression?.status === "running";
  const compressRecording = async () => {
    try {
      await sendJson<{ pid: number }>(`${base}/recording/compress`);
      onMessage(`${run.name}: compressing the recording to 720p`, false);
      onChanged();
    } catch (error) {
      reportFailure("the recording was not compressed", error);
    }
  };
  const recordingItems = [
    // An audio recording has no picture to shrink
    ...(isRecordingKept && compression?.status !== "done" && !summary.is_audio ? [{ label: "Compress to 720p",
      hint: "re-encodes the kept video in place to save space; the original is replaced", icon: "download",
      onSelect: () => void compressRecording() }] : []),
    ...(isRecordingKept ? [{ label: `Move ${source} to inbox`, hint: "back in the inbox, phases 1 and 2 can run again", icon: "refresh",
      onSelect: () => void moveRecording("to-inbox") }] : []),
    ...(detail?.can_keep_recording ? [{ label: `Move ${source} to run folder`, hint: "kept there, as a run does when it ends",
      icon: "refresh", onSelect: () => void moveRecording("to-run") }] : []),
    ...(detail?.has_recording ? [{ label: `Move ${source} to trash`, hint: "into the inbox trash; Restore there brings it back",
      icon: "trash", isDanger: true, onSelect: () => void deleteRecording() }] : []),
    ...(detail && !detail.has_recording ? [{ label: `Upload ${source}…`, hint: "saved under the name the run record names",
      icon: "upload", onSelect: () => uploadInput.current?.click() }] : []),
  ];

  // 7. The resets: a phase reset keeps phases up to any one before the last (`reset-to` takes 0 to 5); it opens keeping
  // what is complete
  const resetMax = Math.max(0, ...phases.map((phase) => phase.number)) - 1;
  const openReset = (kind: ResetKind, number?: number) => {
    let defaultNumber = resetMax;
    if (kind === "chapter") defaultNumber = 1;
    else if (firstOpen) defaultNumber = Math.max(0, firstOpen.number - 1);
    setReset({ kind, number: number ?? defaultNumber });
  };

  // 8. The progress card: the bar of the phase chosen, else the running phase, else one that stopped short, else the
  // last that ran; a phase that has not run has no bar to show (the owner, shown phase 3's bar under a run that had just
  // finished phase 2)
  const ranPhases = phaseRows.filter((phase) => hasPhaseRun(phase) && !isPhaseOff(phase));
  const barPhase = phaseRows.find((phase) => phase.number === chosenPhase)
    ?? runningPhase ?? ranPhases.find((phase) => phase.status !== "complete") ?? ranPhases[ranPhases.length - 1];
  const progress = detail ? chooseProgress(detail, barPhase?.number) : null;
  const progressTotal = progress?.counts.reduce((sum, [, count]) => sum + count, 0) ?? 0;
  const listedStages = listStages(detail, barPhase?.number);
  const openWorkerStep = (phaseNumber: number, slug: string) => {
    const phase = phases.find((known) => known.number === phaseNumber);
    const row = phaseRows.find((known) => known.number === phaseNumber);
    if (!phase) return;
    setWorkerStep(slug);
    setWorkerPhase({ ...phase, ...row });
  };
  const toggleDetailsTable = () => setIsDetailsTableOpen(!isDetailsTableOpen);
  const workerButton = barPhase && barPhase.number >= 2 && (
    <button type="button" className="btn ghost small worker-open" title="the instructions and input each step of this phase was sent"
            onClick={() => setWorkerPhase(barPhase)}>
      <Icon name="eye" size={14} /> What the worker saw
    </button>
  );
  // a card's file, read in the Artifacts tab
  const openArtifact = (path: string) => {
    setOpenPath(path);
    setView("artifacts");
  };
  // the published document, opened full screen: shown in the Artifacts tab alone, the button seemed to do nothing on a
  // run already showing it (the owner)
  // the path of the document asked to be read: the published one, or one of its versions
  const [readPath, setReadPath] = useState("");
  const openDocument = (path: string) => {
    openArtifact(path);
    setReadPath(path);
  };

  // 9. Every step that blocked or was reset, with its blocks: how often the run stopped on it, and why; while a phase is
  // blocked it stands open under the phase cards, so why is read before Unblock is pressed
  const troubledSteps = (detail?.step_outcomes ?? []).filter((step) => step.blocked > 0 || step.reset > 0);
  const blockTotal = troubledSteps.reduce((sum, step) => sum + step.blocked, 0);
  const blocksCard = troubledSteps.length > 0 && (
    <details className="card step-blocks" open={Boolean(blockedPhase) || undefined}>
      <summary>
        <span className="caret"><Icon name="chevron" size={13} /></span>
        <strong>Blocks</strong>
        <span className="muted"> · {blockTotal} over {troubledSteps.length} step{troubledSteps.length > 1 ? "s" : ""}</span>
      </summary>
      <ul className="step-list">
        {troubledSteps.map((step) => (
          <li key={`${step.phase}-${step.step}`}>
            <div className="step-row">
              <span className="truncate">phase {step.phase} · {step.step}
                <button type="button" className="btn ghost icon-only tiny" aria-label={`see what the worker saw for ${step.step}`}
                  title="what the worker was sent for this step, and why it blocked" onClick={() => openWorkerStep(step.phase, step.step)}>
                  <Icon name="eye" size={14} />
                </button>
              </span>
              <span className="muted">blocked {step.blocked} · accepted {step.accepted}{step.reset ? ` · reset ${step.reset}` : ""}</span>
            </div>
            <ol className="block-list">
              {step.blocks.map((block, index) => (
                <li key={index}>
                  <small className="muted">{block.at ? formatDateTime(block.at, { withYear: true }) : "no time"}</small> {block.summary}
                  {block.reason && block.reason !== block.summary && <><br /><small className="muted">{block.reason}</small></>}
                </li>
              ))}
            </ol>
          </li>
        ))}
      </ul>
    </details>
  );

  // 10. The newest worker report, open; while a phase is blocked it stands under the Blocks, where Unblock is decided
  const feedback = detail?.worker_feedback;
  // A report is answered once the watcher wrote its decision beside it (worker-feedback-answers.md): the card then
  // says so and what was decided, so a report judged hours before never stands as unread (the owner asked whether
  // one was read while its fix had landed)
  const openFeedback = feedback ? feedback.open ?? feedback.entries : 0;
  const feedbackCard = feedback && feedback.entries > 0 && feedback.reports > hiddenFeedback && (
    <details className={`card worker-feedback${openFeedback === 0 ? " answered" : ""}`} open>
      <summary>
        <span className="caret"><Icon name="chevron" size={13} /></span>
        <span className="summary-text">
          <strong>{openFeedback === 0 ? "Worker reports answered" : "A worker reported a problem"}</strong>
          <span className="muted"> · {feedback.entries} problem{feedback.entries === 1 ? "" : "s"}
            {feedback.reports > feedback.entries ? `, ${feedback.reports} reports in all` : ""}
            {openFeedback > 0 && openFeedback < feedback.entries ? `, ${openFeedback} not answered` : ""}</span>
        </span>
      </summary>
      <button className="btn ghost icon-only feedback-close" aria-label="hide this report until a worker reports another"
        onClick={() => hideFeedback(feedback.reports)}>
        <Icon name="x" size={14} />
      </button>
      <p>
        <code>{feedback.newest_kind}</code> {feedback.newest_summary}
      </p>
      <p className="feedback-answer">
        {feedback.newest_answer ? <><strong>Answered:</strong> {feedback.newest_answer}</> : <strong>Not answered yet</strong>}
      </p>
      <button className="btn small" onClick={() => openArtifact(feedback.file)}>Read {feedback.file}</button>
    </details>
  );
  const critique = detail?.critique;

  // 11. The Copy and run again menu's groups: another profile once there is a document, a PDF's other processing mode (from phase 2, which decides it), another part of the input (from phase 1),
  // and a branch from any phase up to the one after the last the run reached
  // every mode but the run's own: a menu of one other mode never offered the markdown conversion
  const otherModes = PDF_MODES.filter((mode) => mode.value !== pdfMode).map((mode) => mode.value);
  const reachedPhase = Math.max(0, ...phaseRows.filter(hasPhaseRun).map((phase) => phase.number));
  const branchPhases = phaseRows.filter((phase) => phase.number >= 2 && phase.number <= reachedPhase + 1);
  const newFolderItems = [
    // a PDF is never offered the profiles of people speaking: its document changes through its processing below
    ...(summary.has_document && !isPdf ? ingestingProfiles.profiles
      .filter((profile) => profile.name !== summary.ingesting_profile)
      // the group's heading says once what every conversion does; no profile repeats it under its name (the owner)
      .map((profile) => ({ label: `Convert to ${profile.name}`, group: "Another profile",
        icon: "layers", onSelect: () => void convert(profile.name) })) : []),
    ...(pdfMode ? otherModes.map((otherMode) => ({
      label: `${describePdfChoice(otherMode).processing} · ${describePdfChoice(otherMode).document}`,
      group: "Another processing or document", hint: `${describePdfMode(otherMode).hint}, from phase 2`,
      icon: "layers", onSelect: () => void branch(2, otherMode) })) : []),
    // a run whose recording is gone has no part to choose from
    ...(video ? [{ label: isPdf ? "Choose other pages…" : "Choose other times…", group: "Another part",
      hint: summary.input_range ? `this run: ${summary.input_range}; from phase 1, queued` : "from phase 1, queued",
      icon: "range", onSelect: () => setIsPartOpen(true) }] : []),
    ...branchPhases.map((phase) => ({ label: `Branch from phase ${phase.number} · ${formatPhaseTitle(phase)}`,
      group: "From a phase", hint: phase.number === 2 ? "keeps phase 1" : `keeps phases 1 to ${phase.number - 1}`,
      icon: "layers", onSelect: () => void branch(phase.number) })),
  ];

  // 12. The header: the AI name the run was given in the Manager's Rename, else its folder's name; the ingesting
  // profile an audio run's document is written in, which phase 2 picks when no one chose it
  const shownName = detail?.display_name || run.display_name || run.name;
  const topic = detail?.topic || run.topic;
  let ingestingProfileText = "Ingesting profile: chosen in phase 2";
  if (summary.is_profile_chosen) ingestingProfileText = `Ingesting profile: ${summary.ingesting_profile}`;
  else if (phaseRows.find((phase) => phase.number === 2)?.status === "complete") {
    ingestingProfileText = `Ingesting profile: ${summary.ingesting_profile} (default)`;
  }
  const branchedFrom = detail?.branch?.from;
  const documentFile = detail?.document_file;
  // A finished run's actions stand at the right of its title, as Start run does on a new video (the owner); it offers no
  // resume, since nothing the document needs is left to run
  const isFinished = status === "complete" && !isLive;
  // the frames phase 2 kept, whatever their state in phase 3: the run time is divided by them
  const frameCount = detail ? Object.values(detail.frames).reduce((sum, count) => sum + count, 0) : 0;
  const saveFrom = (url: string) => {
    const link = document.createElement("a");
    link.href = url;
    link.download = "";
    link.click();
  };
  // Open is the button, Download is in its menu (the owner); the menu lists every version, the one document alone too,
  // each opened by its name and saved by the button on its row, and with two or more one item saves them all
  const documentVersions = detail?.document_versions ?? [];
  // each version links its figures as the published document does, so its links are read from the published folder
  const publishedFolder = documentFile ? documentFile.slice(0, documentFile.lastIndexOf("/") + 1) : "";
  // a part's document too, opened from phase 7's table of parts: the last part's is no version of the menu
  // every worker that ran or runs, in phase order: a phase's worker as its card's eye button opens it, then each phase 7
  // part that started, for the Artifacts tab's Worker turns
  const phaseSessions: WorkerSession[] = phaseRows
    .filter((phase) => phase.number !== 7 && phase.has_worker && hasPhaseRun(phase))
    .map((phase) => ({ key: `phase-${phase.number}`, label: `Phase ${phase.number} · ${phase.title || phase.name}`,
      readPath: `${base}/phases/${phase.number}/worker`, isRunning: phase.status === "running" }));
  const partSessions: WorkerSession[] = (detail?.document_pass_parts ?? [])
    .filter((part) => part.status !== "waiting")
    .map((part) => ({ key: `part-${part.number}`, label: `Phase 7 · part ${part.number}`,
      readPath: `${base}/document-pass/parts/${part.number}`, isRunning: part.status === "running" }));
  const workerSessions = [...phaseSessions, ...partSessions];
  const versionLinkFolders = Object.fromEntries([...documentVersions.map((version) => version.path),
    ...(detail?.document_pass_parts ?? []).map((part) => part.document ?? "").filter(Boolean)]
    .map((path) => [path, publishedFolder]));
  const documentButton = documentFile && (
    <div className="split document-open">
      <button className="btn" onClick={() => openDocument(documentFile)}><Icon name="eye" /> Open document</button>
      {documentVersions.length ? (
        <Dropdown icon="chevron" variant="icon-only" align="right" title="more ways to get the document" popup="menu">
          {(_close, closeAndRefocus) => (
            <ul className="menu document-versions" role="menu">
              <li role="none"><PanelNote text="Each version opens by its name; the button beside it saves it with its figures." /></li>
              {documentVersions.map((version) => (
                <li key={version.number} role="none" className="version-row">
                  <button role="menuitem" onClick={() => {
                    closeAndRefocus();
                    openDocument(version.path);
                  }}>
                    <span className="menu-text"><span>{version.number} · {version.label}</span>
                      {version.path === documentFile && <small>the published document</small>}</span>
                  </button>
                  <button className="btn ghost icon-only" aria-label={`download version ${version.number}`}
                    title="download this version" onClick={() => saveFrom(buildVersionsDownloadUrl(base, version.number))}>
                    <Icon name="download" />
                  </button>
                </li>
              ))}
              {documentVersions.length > 1 && (
                <li role="none" className="menu-split">
                  <button role="menuitem" onClick={() => {
                    closeAndRefocus();
                    saveFrom(buildVersionsDownloadUrl(base));
                  }}>
                    <Icon name="download" />
                    <span className="menu-text"><span>Download all versions</span><small>one zip, with the figures</small></span>
                  </button>
                </li>
              )}
            </ul>
          )}
        </Dropdown>
      ) : (
        <Menu icon="chevron" variant="icon-only" align="right" title="more ways to get the document" items={[
          { label: "Download document", hint: "a document with figures comes as a zip", icon: "download",
            onSelect: () => saveFrom(buildDownloadUrl(base, documentFile)) },
        ]} />
      )}
    </div>
  );
  const isRecordingMenuShown = canStart && !recordingAction && !isCompressing && recordingItems.length > 0;
  const runLabel = isAllComplete ? "Run again" : "Run";
  // The ··· menu holds what is used least (the owner, asking for the buttons' order: the main action first, the
  // document's buttons next): running chosen phases, the recording's or PDF's actions, and the resets
  const moreItems = [
    { label: `${runLabel}…`, hint: "choose the phases, models and settings, then queue", icon: "layers", group: "Run",
      onSelect: () => setIsRunOpen(true) },
    ...(isRecordingMenuShown ? recordingItems.map((item) => ({ ...item, group: isPdf ? "PDF" : "Recording" })) : []),
    { label: "Reset to phase…", hint: "remove the phases after the one you choose", icon: "reset", group: "Reset",
      onSelect: () => openReset("phase") },
    { label: "Reset from chapter…", hint: isDocumentStarted ? "write the chosen chapter and later ones again"
      : "phase 4 has written no chapter yet", icon: "reset", group: "Reset", isDisabled: !isDocumentStarted,
      onSelect: () => openReset("chapter") },
  ];
  // Change this document, a new run folder and the ··· menu: in the toolbar while the run goes, beside the title once
  // it is done
  const runActions = (
    <>
        {canStart && isUnblockOpen && blockedPhase && (
          <Dialog title={`Unblock phase ${blockedPhase.number} · ${formatPhaseTitle(blockedPhase)}`} onClose={() => setIsUnblockOpen(false)}>
            <div className="run-choices">
              <p className="muted">The blocked step is asked again, then the run goes on. Nothing runs until you press Run it now.</p>
              <ProviderSetChoice sets={providerSets} selection={{ ...NO_SELECTION, provider_set: unblockSet }}
                onChange={(next) => setUnblockSet(next.provider_set ?? "")} />
              <button className="btn primary block" onClick={() => {
                setIsUnblockOpen(false);
                void queue({ ...runChoice, is_resuming: true, ...(unblockSet ? { provider_set: unblockSet } : {}) }, "an unblock");
              }}>
                <Icon name="play" /> Run it now
              </button>
              <button className="btn block" onClick={() => setIsUnblockOpen(false)}>Not now: leave the run blocked</button>
            </div>
          </Dialog>
        )}
        {canStart && isRunOpen && (
          <Dialog title={runLabel} onClose={() => setIsRunOpen(false)}>
            <div className="run-choices">
              {/* tabs, as the inbox's Run settings: folded parts stacked one long column taller than the screen (the
                  owner: "ugly layout ... missing scroll"); a video run's Workflow speed is a dropdown on top of the
                  Settings tab (the owner: "it can be in the setting tab, its only a drop down"), the same choice as
                  the header's Speed pill, and the run queued here takes it */}
              <PhaseSelector phases={runnablePhases} phaseModels={phaseModels}
                selection={fitSelection(selection)} onChange={setSelection} isTagsShown={false} layout="tabs"
                settingsExtraSummary={detail?.workflow && runDefaults?.speed ? `speed ${describeSpeed(runChoice, runDefaults)}` : ""}
                settingsExtra={detail?.workflow && runDefaults?.speed ? <div className="field">
                    <span>Workflow speed</span>
                    <WorkflowSpeedChoice selection={runChoice} defaults={runDefaults}
                      onChange={(next) => setSpeedChoice({ speed_preset: next.speed_preset, speed_settings: next.speed_settings })} />
                  </div> : null} />
              {isRecordingLocked && <p className="muted">{lockReason}</p>}
              <button className="btn primary block" onClick={() => {
                setIsRunOpen(false);
                void queue({ ...fitSelection(selection), ...runSpeed }, "a run");
              }}>
                Queue this run
              </button>
            </div>
          </Dialog>
        )}
        {/* A done PDF's document cleaned up, rewritten or written as knowledge by a model, as phase 7 in this run
            folder, with the person's wishes; the document before is kept in
            07_reshaping/before.md */}
        {canStart && isPdf && summary.has_document && (
          <Dropdown label="Change this document" icon="layers" panelClassName="wide">
            {(close) => (
              <DocumentPassPanel profile={run.settings_profile ?? ""} lastPass={summary.document_pass ?? ""}
                onQueue={(pass, wishes, source) => {
                close();
                void queue({ ...NO_SELECTION, only_phase: 7, is_resuming: true, document_pass: pass,
                  document_wishes: wishes, document_source: source }, `a ${describeDocumentPass(pass)}`);
              }} />
            )}
          </Dropdown>
        )}
        {/* A done audio run's document written again in another profile, in this run folder, from the same notes; the
            document it has stays a version */}
        {canStart && !isPdf && summary.has_document && (detail?.appendable_profiles ?? []).length > 0 && (
          <Menu label="Change this document" icon="layers"
            note="Writes the document again in this run; the one it has stays in the Open document menu."
            help={<p>Only the profiles that read this run's notes as they are: phase 4 runs again, and nothing before it.
              A profile that needs other notes is under Copy and run again.</p>}
            items={(detail?.appendable_profiles ?? []).map((profile) => ({ label: `Write as ${profile}`,
              group: "Another profile", icon: "layers", onSelect: () => void writeInPlace(profile) }))} />
        )}
        {/* Every way to copy this run into a new run folder, in one menu (the owner): another profile, a PDF's other
            processing mode or reader, a branch from a phase. Nothing this run made is replaced. */}
        {canStart && newFolderItems.length > 0 && (
          <Menu label="Copy and run again" icon="layers"
            note="Makes a new run folder beside this one; this run stays as it is."
            help={<CopyMenuHelp groups={[...new Set(newFolderItems.map((item) => item.group))]} />}
            items={newFolderItems} />
        )}
        {isPartOpen && (
          <AnotherPartDialog runBase={base} runName={shownName} onSubmit={runPart} onClose={() => setIsPartOpen(false)} />
        )}
        {canStart && (
          <Menu icon="more" variant="icon-only" align="right" title="more actions" items={moreItems} />
        )}
    </>
  );

  return (
    <div className="detail">
      <header className="detail-head">
        <button className="btn ghost icon-only back" onClick={onBack} aria-label="back"><Icon name="back" /></button>
        <div className="detail-title">
          <RunTitle shownName={shownName} folderName={run.name} onSave={async (name) => {
            try {
              await saveRunDisplayName(buildRunBase(run), name);
              onChanged();
            } catch (error) {
              reportFailure("not renamed", error);
            }
          }} />
          {topic && !isSameName(topic, shownName) && (
            <div className="topic detail-topic truncate" title={topic}>{topic}</div>
          )}
          <small className="muted truncate" title={run.path}>{run.path} · {detail?.run_id || run.run_id || "no run record"}</small>
          {branchedFrom && (
            <small className="muted">branched from{" "}
              <button type="button" className="link-button" aria-label={`branched from ${branchedFrom}`}
                onClick={() => onOpenRun(run.root, branchedFrom)}>{branchedFrom}</button>
              {" "}at phase {detail?.branch?.phase}</small>
          )}
          <RunTags tags={summary.tags ?? []} onChanged={onChanged} save={async (tags) => {
            const answer = await saveRunTags(base, tags);
            if (answer.exit_code !== 0) throw new Error(answer.output);
          }} />
        </div>
        <div className="head-pills">
          {isLive && status !== "running" && <span className="pill running pulse" title={`process ${detail?.live_pids.join(", ")}`}>live</span>}
          {/* a request is for a live run; one left in the file of a run that has ended is cleared at its next start */}
          {isLive && detail?.signal && <span className="pill warn">
            {detail.signal === "pause" && detail.is_held ? "paused" : `${detail.signal} requested`}</span>}
          {status === "running" && detail && !isLive ? (
            <span className="pill stopped" title="its record says running, but no process runs it: Resume goes on from where it stopped">
              stopped
            </span>
          ) : <span className={`pill ${status}`}>{formatRunState(status)}</span>}
          {/* The ingesting profile the document is (or will be) written in: the shape of the final artifact. An audio
              run's is picked by phase 2, so until then it says so rather than show the default (the owner: "i should
              see it as soon as it choesd") */}
          {/* a video run's Workflow speed, which its next Resume or "Run from" takes (the owner: presets that change
              the settings affecting the speed, "custom" once one is changed) */}
          {detail?.workflow && (
            <span className="pill profile speed-pill" title="the Workflow speed: the run's next resume or phase run takes it">
              Speed: {runDefaults?.speed ? <WorkflowSpeedChoice selection={runChoice} defaults={runDefaults}
                onChange={(next) => setSpeedChoice({ speed_preset: next.speed_preset, speed_settings: next.speed_settings })} />
                : nameSpeedPreset(detail.speed?.name ?? (isFastWorkflow(detail.workflow) ? "fast" : "quality"))}
            </span>
          )}
          {/* the provider set the run is on, and its machine: its own choice, else the default set, which a run that
              never chose one is on (the owner saw no provider set on such a run) */}
          {(detail?.provider_set || providerSets) && (
            <span className="pill profile" title="the provider set: the models and hosts this run's next phases call">
              Provider set: {nameProviderSet(providerSets?.sets.find((entry) => entry.name === (detail?.provider_set || providerSets.default_set))
                ?? { name: detail?.provider_set || providerSets?.default_set || "default" })}
            </span>
          )}
          <span className="pill profile" title="the settings the run read: the default settings, and a settings profile on top">
            Settings: {(isMadeWithProfileGone ? "" : madeWithProfile) || "default"}
          </span>
          {/* a PDF run's processing mode, chosen on the run form (the owner: "seleced mode should be shown as pill") */}
          {isPdf && (
            <>
              <span className="pill profile" title="how the PDF is processed: its text, or each page as a picture">
                Processing: {describePdfChoice(pdfMode).processing}
              </span>
              <span className="pill profile"
                title="how the document is made: markdown, then the LLM pass it was given, or written as knowledge">
                Document: {describePdfChoice(pdfMode, summary.document_pass ?? "").document}
              </span>
            </>
          )}
          {/* the ingesting profile shapes an audio run's document; a video run has none to show (the owner) */}
          {/* a PDF read as text takes the audio route from phase 4 on; its Document pill above says how it is shaped */}
          {summary.is_audio && !isPdf && (
            <span className="pill profile" title="the ingesting profile the document is written in">{ingestingProfileText}</span>
          )}
          {/* the part of the input the run processes, when the run form or --start/--end/--pages chose one */}
          {summary.input_range && (
            <span className="pill profile" title="only this part of the input is processed">
              Part: {summary.input_range}
            </span>
          )}
        </div>
        {/* the run time is text at the top right, above a finished run's actions, never a pill (the owner) */}
        {((detail && detail.timing.total_s > 0) || isFinished) && (
          <div className="head-right">
            {detail && detail.timing.total_s > 0 && (
              <span className={`run-time${detail.timing.is_counting ? " counting" : ""}`}
                title="time spent on this run folder, over every run and resume">
                {/* two labelled rows, labels left and values right (the owner's wording) */}
                <span className="run-time-label">Total processing time</span>
                <span className="run-time-value">{formatDuration(detail.timing.total_s)}</span>
                {/* the processing time over every phase divided by the frames kept, live with it (the owner) */}
                {/* before phase 2 has kept its frames there is nothing to divide by, and the row is left out: a line
                    saying when it comes was too long (the owner) */}
                {frameCount > 0 && <>
                  <span className="run-time-label">Each frame</span>
                  <span className="run-time-value" title="the processing time over every phase so far, divided by the frames phase 2 kept">
                    {`${formatDuration(detail.timing.total_s / frameCount)} on average`}
                  </span>
                </>}
              </span>
            )}
            {isFinished && (
              <div className="head-actions">
                {documentButton}
                {runActions}
              </div>
            )}
          </div>
        )}
      </header>

      {canStart && isMadeWithProfileGone && (
        <p className="settings-note" role="status">
          <Icon name="help" size={14} /> This run's settings profile {madeWithProfile} no longer exists: a resume or a new run
          here uses the default settings.
        </p>
      )}
      {isLive && detail?.has_newer_settings && (
        <p className="settings-note" role="status">
          <Icon name="help" size={14} /> Settings changed after this run started. It goes on with the ones it read; stop it
          and resume it to use the new ones.
        </p>
      )}
      {/* a request waiting for the run's next check is said in words where the run's state is read, never only as one
          small pill among the header's (the owner pressed Stop and saw nothing say it would stop) */}
      {isLive && detail?.signal && (
        <p className="settings-note signal-note" role="status">
          <Icon name={detail.signal === "pause" ? "pause" : "stop"} size={14} /> {describeSignal(detail.signal, Boolean(detail.is_held))}
        </p>
      )}
      <div className="toolbar">
        {isLive && (
          <>
            <span className="state-note"><span className="dot running" /> {runningPhase ? `Running phase ${runningPhase.number} · ${formatPhaseTitle(runningPhase)}` : "Running"}
              {/* what it does right now: a run reading frames' text for minutes looked stuck beside an idle model host */}
              {detail?.activity && !detail.is_held && (
                <small className="activity-note">{detail.activity.text}, since {detail.activity.since.slice(11, 16)}</small>
              )}
            </span>
            <Menu label="Control" icon="pause" items={[
              { label: "Pause", hint: "hold at the next check", icon: "pause", isDisabled: detail?.signal === "pause",
                onSelect: () => void signal("pause") },
              { label: "Drain", hint: "finish the current item, then stop", icon: "list",
                isDisabled: detail?.signal === "drain", onSelect: () => void signal("drain") },
              { label: "Stop", hint: "stop at the next check", icon: "stop", isDanger: true, isDisabled: detail?.signal === "stop",
                onSelect: () => void signal("stop") },
              { label: "Interrupt now", hint: runningJob ? "as Ctrl-C would, without waiting for a check" : "only for a run started from this page",
                icon: "x", isDanger: true, isDisabled: !runningJob, onSelect: () => { if (runningJob) void actOnJob(runningJob, "interrupt"); } },
              ...(holdItem ? [{ ...holdItem, group: "If this job fails" }] : []),
            ]} />
            {detail?.signal && (
              <button className="btn" onClick={() => void signal("continue")}>
                <Icon name="play" /> {detail.signal === "pause" ? "Continue" : `Cancel the ${detail.signal}`}</button>
            )}
          </>
        )}
        {waitingJob && !isLive && (
          <>
            <span className="state-note"><span className="dot queued" /> Queued as job {waitingJob.job_id}</span>
            <button className="btn" onClick={() => void actOnJob(waitingJob, "cancel")}><Icon name="x" /> Take off the queue</button>
          </>
        )}
        {retryingJob?.status === "retrying" && !isLive && (
          <button className="btn" onClick={() => void actOnJob(retryingJob, "cancel")} title="no more retries">
            <Icon name="x" /> Take off the queue
          </button>
        )}
        {openJob && <JobFailureControls job={openJob} onChanged={onChanged} onMessage={onMessage}
          holdItem={isLive ? null : holdItem} />}
        {canStart && blockedPhase && (
          <button className="btn primary" onClick={() => { setUnblockSet(detail?.provider_set ?? ""); setIsUnblockOpen(true); }}
            title="opens a window: you choose whether the run goes on now, and on which provider set; nothing runs before that">
            <Icon name="unlock" /> Unblock phase {blockedPhase.number} · {formatPhaseTitle(blockedPhase)}…
          </button>
        )}
        {canStart && blockedFrames.length > 0 && (
          <button className={`btn ${blockedPhase ? "" : "primary"}`}
            onClick={() => void queue({ ...runChoice, worker_indexes: blockedFrames }, `frames ${blockedFrames.join(", ")} again`)}
            title="phase 3 asks only these frames again and rewrites their notes">
            <Icon name="refresh" /> Ask blocked {unit.one}{blockedFrames.length > 1 ? "s" : ""} {blockedFrames.join(", ")} again
          </button>
        )}
        {canStart && !isFinished && firstOpen && !blockedPhase && !(isRecordingLocked && firstOpen.number < firstRunnable) && (
          // one click resumes with the run's own settings; the arrow resumes with other settings, which win over the
          // ones the run was made with (the owner: choosing the local settings in a menu was the only way to move a run
          // from another machine's model to this one's, and the new Resume button had no such menu)
          // the button says only "Resume"; the phase it goes on from is in its name, its tooltip and the arrow's menu
          // (the owner: "Resume from 7 · final document reshaping" made the button too long)
          <div className="split resume-split">
            <button className={`btn ${blockedFrames.length ? "" : "primary"}`}
              aria-label={`Resume from ${firstOpen.number} · ${formatPhaseTitle(firstOpen)}`}
              title={`goes on from phase ${firstOpen.number}, ${formatPhaseTitle(firstOpen)}`}
              onClick={() => void queue({ ...runChoice, is_resuming: true }, "a resume")}>
              <Icon name="play" /> Resume
            </button>
            <Menu icon="chevron" variant="icon-only" align="right" title="resume with another provider set or other settings"
              note={`Goes on from phase ${firstOpen.number}, ${formatPhaseTitle(firstOpen)}, on the provider set or with the settings you choose, in place of the run's own.`}
              items={[...(providerSets && providerSets.sets.length > 1 ? providerSets.sets.map((entry) => ({
                label: `provider set: ${nameProviderSet(entry)}`, group: "Resume on", hint: entry.name === detail?.provider_set ? "the run's own"
                  : !detail?.provider_set && entry.name === providerSets.default_set ? "the default" : "",
                onSelect: () => void queue({ ...runChoice, is_resuming: true, provider_set: entry.name },
                  `a resume on provider set ${entry.name}`) })) : []),
                ...["", ...(settingsProfiles.profiles ?? [])].map((profile) => ({
                label: profile ? `profile: ${profile}` : "default settings", group: "Resume with",
                hint: profile === runChoice.profile ? "the run's own" : profile === settingsProfiles.serverProfile ? "the server's" : "",
                onSelect: () => void queue({ ...runChoice, is_resuming: true, profile },
                  `a resume with ${profile ? `profile ${profile}` : "the default settings"}`) }))]} />
          </div>
        )}
        {/* the published document, after the main action: read here, or saved */}
        {!isFinished && documentButton}
        {canStart && recordingAction && (
          <span className="state-note"><span className="spinner" aria-hidden="true" /> {describeRecordingWork(recordingAction, source)}</span>
        )}
        {isCompressing && (
          <span className="state-note"><span className="spinner" aria-hidden="true" /> Compressing recording to 720p…
            {compression?.progress ? ` ${Math.round(compression.progress * 100)}%` : ""}</span>
        )}
        {compression?.status === "done" && (
          <span className="state-note">Recording compressed · {formatSize(compression.before_bytes)} → {formatSize(compression.after_bytes)}</span>
        )}
        {(compression?.status === "failed" || compression?.status === "stopped") && (
          <span className="state-note error" title={compression.error}>
            Compressing the recording {compression.status === "failed" ? "failed" : "stopped before it ended"}
          </span>
        )}
        {canStart && detail && !detail.has_recording && (
          <input ref={uploadInput} type="file" accept="video/*,audio/*,.pdf,.docx,.odt,.pptx,.odp,.m4a,.opus" hidden aria-label="recording file" onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            if (file) void uploadRecording(file);
          }} />
        )}
        {!isFinished && runActions}
      </div>

      {livePhase !== null && (
        <WorkerLive title={`Phase ${livePhase}`} label={`phase ${livePhase}`} runBase={base}
          readPath={`${base}/phases/${livePhase}/worker`} sendPath={`${base}/phases/${livePhase}/worker/messages`}
          isRunning={phaseRows.find((phase) => phase.number === livePhase)?.status === "running"}
          canAsk={livePhase === 4 || livePhase === 5} onClose={() => setLivePhase(null)} />
      )}
      {fastSession && (
        <WorkerLive title={`Frames ${fastSession.replace(/^fast-notes-0*(\d+)-0*(\d+)$/, "$1–$2")}`} label="this session"
          runBase={base} readPath={`${base}/fast-sessions/${encodeURIComponent(fastSession)}`}
          sendPath={`${base}/phases/3/worker/messages`} isRunning={false} canAsk={false} onClose={() => setFastSession("")} />
      )}

      <div className="view-tabs" role="tablist">
        <button role="tab" aria-selected={view === "progress"} className={view === "progress" ? "on" : ""}
          onClick={() => setView("progress")}>Progress</button>
        <button role="tab" aria-selected={view === "artifacts"} className={view === "artifacts" ? "on" : ""}
          onClick={() => setView("artifacts")}>Artifacts</button>
        {detail?.has_recording && (
          <button role="tab" aria-selected={view === "recording"} className={view === "recording" ? "on" : ""}
            onClick={() => setView("recording")}>{isPdf ? "PDF" : "Recording"}</button>
        )}
      </div>

      {view === "artifacts" && (
        <ArtifactsView key={(summary.tags ?? []).join(",")} runBase={base} isLive={isLive} hasRecording={Boolean(detail?.has_recording)} isAudio={summary.is_audio}
          openPath={openPath} onOpened={() => setOpenPath("")}
          readPath={readPath} onRead={() => setReadPath("")} linkFolders={versionLinkFolders} workerSessions={workerSessions}
          canChat={Boolean(summary.has_document) && !isLive}
          onPlayFrom={(seconds) => {
            setView("recording");
            setSeekTo(seconds);
          }} />
      )}
      {view === "recording" && detail?.has_recording && (
        <RecordingView runBase={base} name={video.split("/").pop() ?? "recording"}
          parts={detail.recording_parts} isAudio={summary.is_audio} batches={detail.batches ?? []} onPlayFrom={setSeekTo}
          seekTo={seekTo} onSeeked={clearSeek} />
      )}

      {view === "progress" && (
      <>
      <ol className="stepper">
        {phaseRows.map((phase) => {
          let phaseWord = phase.status;
          if (isPhaseOff(phase)) phaseWord = "skipped";
          else if (phase.status === "not run") phaseWord = hasPhaseRun(phase) ? "stopped" : "pending";
          return (
          <li key={phase.number}
            className={`step ${isPhaseOff(phase) ? "off" : phase.status.replace(/ /g, "-")}${phase.number === chosenPhase ? " chosen" : ""}${phase.number === barPhase?.number ? " shown" : ""}`}>
            <button type="button" className="step-choose" aria-pressed={phase.number === chosenPhase}
              title={isPhaseOff(phase) ? `skipped: phase ${phase.number} is switched off in the settings` : "show this phase's progress"}
              onClick={() => setChosenPhase(phase.number === chosenPhase ? null : phase.number)}>
            <span className="step-dot">{phase.status === "complete" ? <Icon name="check" size={12} /> : phase.number}</span>
            <span className="step-text">
              <span className="step-name">{formatPhaseTitle(phase)}</span>
              <small>
                {phaseWord}
                {phase.note && <span className="step-note"> · {phase.note}</span>}
                {detail?.timing.phases[phase.name] !== undefined && (
                  <span className="step-time"> · {formatDuration(detail.timing.phases[phase.name])}</span>
                )}
              </small>
            </span>
            </button>
            {/* the help and the worker sit in one row in the card's top-right corner (the owner: the eye under the "?"
                was "in ugly position"); each is a button of its own beside the one that chooses the phase, read after it.
                The worker: live while it runs, what it did after (the owner: "should be possible to see live thinking
                and steps" in every workflow); phase 7's parts have their own panel */}
            <span className="step-own step-help-corner">
              {phase.number !== 7 && phase.has_worker && hasPhaseRun(phase) && (
                <button type="button" className={`btn ghost icon-only tiny step-worker${phase.status === "running" ? " live" : ""}`}
                  aria-label={`open the worker of phase ${phase.number}`}
                  title={phase.status === "running" ? "watch the worker live" : "what the worker thought and did"}
                  onClick={() => setLivePhase(phase.number)}><Icon name="eye" size={14} /></button>
              )}
              <PhaseHelp phase={{ ...phases.find((known) => known.number === phase.number), ...phase }} />
            </span>
            {canStart && phase.number >= firstRunnable && (
              <span className="step-own">
              <Menu icon="more" variant="ghost icon-only small" align="right" title={`phase ${phase.number} actions`} items={[
                { label: `Run from ${phase.number}`, hint: "this phase and every later one", icon: "play",
                  onSelect: () => void queue({ ...runChoice, from_phase: phase.number }, `phases ${phase.number} onward`) },
                { label: `Run only ${phase.number}`, hint: "read the others from disk", icon: "play",
                  onSelect: () => void queue({ ...runChoice, only_phase: phase.number }, `phase ${phase.number}`) },
                { label: `Reset to ${phase.number - 1}`, hint: !hasPhaseRun(phase) ? "nothing to remove: it has not run"
                  : `remove phase ${phase.number} and later`, icon: "reset", isDisabled: !hasPhaseRun(phase),
                  isDanger: true, onSelect: () => openReset("phase", phase.number - 1) },
              ]} />
              </span>
            )}
          </li>
          );
        })}
      </ol>

      {blockedPhase && blocksCard}
      {blockedPhase && feedbackCard}

      {/* a phase with no steps to count still offers what its worker saw: when chosen, and while it runs or blocked */}
      {!(progress && progressTotal > 0) && barPhase
        && (chosenPhase !== null || ["running", "blocked", "failed"].includes(barPhase.status)) && (
        <div className="card frames">
          <div className="frames-head">
            {/* phase 5's steps open in a table as phase 4's do, read from its conversation */}
            {barPhase.number === 5 && barPhase.status !== "not run" ? (
              <DetailsToggle isOpen={isDetailsTableOpen} onToggle={toggleDetailsTable} noun="step"
                title="every step of the review: its status, calls, time, tokens and rejections">{formatPhaseTitle(barPhase)}</DetailsToggle>
            ) : <strong>{formatPhaseTitle(barPhase)}</strong>}
            {workerButton}
          </div>
          <small className="muted">{barPhase.status === "not run" && "this phase has not run yet"}
            {barPhase.status !== "not run" && barPhase.number === 5 && "its steps are in the table the title opens; its time is on its card"}
            {barPhase.status !== "not run" && barPhase.number !== 5 && "this phase records no steps to count: its time is on its card"}</small>
          {barPhase.number === 5 && barPhase.status !== "not run" && isDetailsTableOpen && (
            <DocumentSteps runBase={base} source="review-steps" isLive={isLive} onOpenStep={(slug) => openWorkerStep(5, slug)}
              refreshKey={JSON.stringify([barPhase.status, detail?.timing.phases.review ?? 0])} />
          )}
        </div>
      )}

      {progress && progressTotal > 0 && (
        <div className="card frames">
          <div className="frames-head">
            {progress.title === "Frames" && (
              <DetailsToggle isOpen={isDetailsTableOpen} onToggle={toggleDetailsTable} noun="batch"
                title="every batch: its time, tokens, result and rejections">{unit.many}</DetailsToggle>
            )}
            {progress.title !== "Frames" && listedStages.length > 0 && (
              <DetailsToggle isOpen={isDetailsTableOpen} onToggle={toggleDetailsTable} noun="stage"
                title="every stage of this phase: its status, frames, time and time per frame">{progress.title}</DetailsToggle>
            )}
            {progress.title !== "Frames" && listedStages.length === 0 && barPhase?.number === 4 && (
              <DetailsToggle isOpen={isDetailsTableOpen} onToggle={toggleDetailsTable} noun="step"
                title="every step of the document writing: its stage, status, calls, time, tokens and rejections">{progress.title}</DetailsToggle>
            )}
            {progress.title !== "Frames" && listedStages.length === 0 && barPhase?.number === 7 && (
              <DetailsToggle isOpen={isDetailsTableOpen} onToggle={toggleDetailsTable} noun="part"
                title="every part of the pass: its status, blocks, model calls and time">{progress.title}</DetailsToggle>
            )}
            {progress.title !== "Frames" && listedStages.length === 0 && barPhase?.number !== 4 && barPhase?.number !== 7
              && <strong>{progress.title}</strong>}
            <span className="muted">{progress.detail ? `${progress.detail} · ` : ""}{progressTotal} in all</span>
            {workerButton}
          </div>
          <div className={`frame-bar${isLive ? " live" : ""}`}>
            {progress.counts.map(([name, count]) => (
              <span key={name} className={`seg ${name}`} style={{ flexGrow: count }} title={`${name} ${count}`} />
            ))}
          </div>
          <div className="legend">
            {progress.counts.map(([name, count]) => (
              <span key={name}><i className={`seg ${name}`} /> {name} {count}</span>
            ))}
            {progress.pace && (
              <span className="pace"><Icon name="gauge" size={13} /> {progress.pace}</span>
            )}
            {progress.lastProgress && (
              <span className={`last-progress${progress.isStalled ? " stalled" : ""}`}
                title="when the running stage last counted one more page; orange after 10 minutes with none">
                {progress.lastProgress}
              </span>
            )}
            {progress.note && <small className="muted">{progress.note}</small>}
          </div>
          {progress.title === "Frames" && isDetailsTableOpen && (
            <BatchHistory runBase={base} refreshKey={JSON.stringify(detail?.frames ?? {})} onOpenStep={(slug) => openWorkerStep(3, slug)}
              onOpenSession={setFastSession} unitTitle={unit.many} />
          )}
          {isDetailsTableOpen && listedStages.length > 0 && barPhase && (
            <StageTable runBase={base} stages={listedStages} phaseNumber={barPhase.number} />
          )}
          {barPhase?.number === 7 && isDetailsTableOpen && (
            <PassPartsTable runBase={base} parts={detail?.document_pass_parts ?? []} onOpenPart={openDocument}
              linkFolder={publishedFolder} />
          )}
          {progress.title !== "Frames" && barPhase?.number === 4 && isDetailsTableOpen && (
            <DocumentSteps runBase={base} isLive={isLive} onOpenStep={(slug) => openWorkerStep(4, slug)}
              refreshKey={JSON.stringify([detail?.document_stages ?? [], detail?.timing.phases.finalize ?? 0])} />
          )}
        </div>
      )}

      {!blockedPhase && feedbackCard}

      {critique && critique.rules > 0 && (
        <details className="card run-critique">
          <summary>
            <span className="caret"><Icon name="chevron" size={13} /></span>
            <strong>Critique</strong>
            <span className="muted"> · {critique.anti_patterns} anti-pattern{critique.anti_patterns === 1 ? "" : "s"}
              {" "}from {critique.rules} rule{critique.rules === 1 ? "" : "s"} over {critique.files} batch{critique.files === 1 ? "" : "es"}</span>
          </summary>
          <p className="muted">{critique.top}</p>
          <p className="muted">Seen in {critique.top_batches} batch{critique.top_batches === 1 ? "" : "es"},
            {" "}{critique.top_frames} frame{critique.top_frames === 1 ? "" : "s"}. The rules each batch reported are in its
            {" "}critique; phase 6 collects them into one rule set.</p>
          {critique.rule_set && (
            <button className="btn small" onClick={() => openArtifact(critique.rule_set)}>Open the rule set</button>
          )}
        </details>
      )}

      {!blockedPhase && blocksCard}

      <LogView title={detail?.log ? detail.log.split("/").slice(-2).join("/") : "Log"} text={text}
        empty={detail ? "No log yet." : "Loading the log …"} />
      </>
      )}

      {workerPhase && <WorkerView runBase={base} phase={workerPhase} isAudio={summary.is_audio} initialStep={workerStep}
        onClose={() => {
          setWorkerPhase(null);
          setWorkerStep("");
        }} />}

      {reset && (
        <ResetDialog key={`${reset.kind}-${reset.number}`} runBase={base} runName={run.name} kind={reset.kind} initialNumber={reset.number}
          phases={runPhases} resetMax={resetMax} chapters={detail?.chapters ?? []} onClose={() => setReset(null)}
          onReset={onChanged} onMessage={onMessage} />
      )}
    </div>
  );
}
