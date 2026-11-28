import { useEffect, useRef, useState } from "react";
import { mergeTags } from "./TagInput";
import { DocumentPassPanel, isProfileGone, PdfChoices, usePhaseModels, useRunDefaults, useSettingsProfiles } from "./RunChoices";
import WorkflowSpeedChoice, { describeSpeed, readSpeedOffPhases } from "./WorkflowSpeedChoice";
import type { DragEvent, ReactNode } from "react";
import { buildRunBase, buildRunPath, choosePdfMode, describeDocumentPass, describeError, describePdfFlow, downloadDocuments, fetchDocumentVersions, describePdfMode, fetchJson, PDF_PROCESSINGS, PDF_SCAN_ENGINES, describePdfShaping, readPdfProcessing, readPdfShaping, fetchRunLists, formatPhaseName, formatRunState, formatSize, isRunFound, isSameName, moveToList, NO_SELECTION, queueJobs, readResetOutput, renameInboxVideo, RUN_SETTINGS_CHOICE_KEY, saveRunLists, sendJson } from "../api";
import type { ArchiveEntry, CommandOutput, DocumentPass, DocumentSource, DownloadVersion, InboxItem, RunDocumentVersions, Job, JobRequest, Phase, PhaseSelection, RunDefaults, RunList, RunSettingsChoice, RunSummary } from "../api";
import { describeMoveOutcomes, moveBack, moveRows } from "../inboxMoves";
import type { InboxPlace } from "../inboxMoves";
import { countJobsAhead, describeChangePill, describeQueued } from "../jobs";
import { toggleInSet } from "../sets";
import { usePersistentState } from "../viewState";
import Dialog from "./Dialog";
import Icon from "./Icon";
import SearchBox from "./SearchBox";
import InboxTrash from "./InboxTrash";
import InboxUpload from "./InboxUpload";
import MergeRecordingsDialog from "./MergeRecordingsDialog";
import VersionAskDialog from "./VersionAskDialog";
import type { UploadHandle } from "./InboxUpload";
import PhaseSelector, { describeSelection } from "./PhaseSelector";
import Spinner from "./Spinner";

// What the run settings read with nothing changed: every phase, on the default settings and models
const DEFAULT_RUN_SETTINGS = "every phase";
// the type bar's choices: every row, then each file type a row's pill names
const FILE_TYPE_FILTERS = ["all", "video", "audio", "pdf", "office", "ppt"];
// a list's types in the order its type bar shows them, and the word each chip reads
const FILE_TYPE_ORDER = ["video", "audio", "pdf", "office", "ppt"];
const FILE_TYPE_LABELS: Record<string, string> = { "": "All", video: "Videos", audio: "Audio", pdf: "PDFs", office: "Office",
  ppt: "Slides" };
// a Word or OpenDocument file, the office workflow's input
const OFFICE_NAME = /\.(docx|odt)$/i;
// a slide deck, the PPT workflow's input
const SLIDES_NAME = /\.(pptx|odp)$/i;

// The words and colours of the inbox's states. A run folder with no inbox video gets its state by the same checks a
// video's runs get on the server: live is `running`, holding the document (or complete) is `done`, else `started`.
const STATE_PILL_CLASSES: Record<InboxItem["state"], string> = {
  new: "new", running: "running", started: "incomplete", done: "complete",
};

// Where a row stands in its section, by the pill it shows: running first, then queued, then what stopped or failed,
// then new, and done at the bottom (the owner: "sorted via status, done at the bottom", in every list); a pill not
// named here stands with what stopped
const PILL_RANKS: Record<string, number> = {
  running: 0, retrying: 0, queued: 1, new: 3, complete: 4, finished: 4,
};
const STOPPED_PILL_RANK = 2;

export function rankPill(pillClass: string): number {
  return PILL_RANKS[pillClass] ?? STOPPED_PILL_RANK;
}

// A started run's pill names the phase it stopped in ("phase 3 ›"), and its title the phase's name; any other state
// names the state; a running run's pill names its provider set when one is given ("running · Mac ›")
function labelStatePill(state: InboxItem["state"], stoppedPhase: number, isBlocked = false, providerSet = ""): string {
  if (state === "started" && stoppedPhase > 0) return `${isBlocked ? "blocked in phase" : "phase"} ${stoppedPhase} ›`;
  if (state === "running" && providerSet) return `${formatRunState(state)} · ${providerSet} ›`;

  return `${formatRunState(state)} ›`;
}

// A blocked run's pill is red, so the runs to unblock stand out from the ones that only stopped
function classifyStatePill(state: InboxItem["state"], isBlocked = false): string {
  return state === "started" && isBlocked ? "failed" : STATE_PILL_CLASSES[state];
}

function titleStoppedPhase(stoppedPhase: number, phases: Phase[]): string {
  const phase = phases.find((candidate) => candidate.number === stoppedPhase);
  return phase ? `stopped in phase ${stoppedPhase}, ${formatPhaseName(phase.name)}` : "";
}

// The Process button's words, always the action it takes: the ticked videos, else every new video, else why it takes
// none ("Select videos" read as an instruction, not as a button: the owner did not understand it)
// What the ticked rows are, by their medium: "PDF" when every one is a PDF, "video" when every one is a video, else
// "recording"; a ticked PDF read "Process 1 video"
function describeTickedKind(items: InboxItem[]): string {
  if (items.every((item) => item.video.toLowerCase().endsWith(".pdf"))) return "PDF";
  if (items.every((item) => OFFICE_NAME.test(item.video))) return "document";
  if (items.every((item) => SLIDES_NAME.test(item.video))) return "deck";
  if (items.every((item) => isVideoItem(item))) {
    return "video";
  }
  return "recording";
}

// What the run settings dialog's choices apply to, in its title: the ticked rows by kind, else every new one
function describeRunTarget(tickedItems: InboxItem[], newCount: number): string {
  if (tickedItems.length) return `${tickedItems.length} ${describeTickedKind(tickedItems)}${tickedItems.length === 1 ? "" : "s"} ticked`;
  return `every new recording (${newCount})`;
}

// The PDF choices' line: each one chosen by its name, "default processing", "default document" and "default scans"
// while untouched
function describePdfChoices(selection: PhaseSelection): string {
  const scans = PDF_SCAN_ENGINES.find((choice) => choice.value === selection.pdf_ocr_engine);
  const processing = PDF_PROCESSINGS.find((entry) => entry.value === readPdfProcessing(selection.pdf_mode));
  const shaping = readPdfShaping(selection);
  return [processing ? processing.label : "default processing",
    shaping ? describePdfShaping(shaping) : "default document",
    scans ? `scans by ${scans.label}` : "default scans"].join(", ");
}

function isPdfItem(item: InboxItem): boolean {
  return item.video.toLowerCase().endsWith(".pdf");
}

// a file's type by its name, for a row the server sent none for: "pdf", "office", "audio" or "video"
function describeFileType(video: string): string {
  if (/\.pdf$/i.test(video)) return "pdf";
  if (OFFICE_NAME.test(video)) return "office";
  if (SLIDES_NAME.test(video)) return "ppt";
  return /\.(mp3|m4a|wav|aac|flac|ogg|opus)$/i.test(video) ? "audio" : "video";
}

// a video: not a sound file, not a document; the one kind a video workflow (quality or fast) applies to
function isVideoItem(item: InboxItem): boolean {
  return !item.is_audio && !isPdfItem(item) && !OFFICE_NAME.test(item.video) && !SLIDES_NAME.test(item.video);
}

// The flow every ticked recording takes, as `/api/phases` names it: "pdf-text-only" or "pdf-pages" by the PDF mode the
// settings resolve to, "audio", or "video"; empty when nothing is ticked or the ticked rows take different flows
// Whether a run asked with `choice` is the run `request` already holds: the same phases and the same choices, a field
// left empty and one an older job record lacks counting as the same default
function isSameRunChoice(choice: PhaseSelection, request: JobRequest): boolean {
  const same = (one: unknown, other: unknown) => (one ?? "") === (other ?? "") || (!one && !other);
  const chosenModels = Object.entries(choice.phase_models ?? {}).map(([phase, name]) => `${phase}=${name}`).sort();
  return same(choice.only_phase, request.only_phase) && same(choice.from_phase, request.from_phase)
    && same(choice.to_phase, request.to_phase) && same(choice.is_dry_run, request.is_dry_run)
    && same(choice.profile, request.profile) && same(choice.ingesting_profile, request.ingesting_profile)
    && same(choice.is_run_as_audio, request.is_run_as_audio) && same(choice.workflow, request.workflow)
    && same(choice.speed_preset, request.speed_preset)
    && same(Object.entries(choice.speed_settings ?? {}).map(([key, value]) => `${key}=${value}`).sort().join(" "),
      (request.speed_settings ?? []).slice().sort().join(" "))
    && same(choice.pdf_mode, request.pdf_mode)
    && same(choice.pdf_ocr_engine, request.pdf_ocr_engine)
    && same(choice.document_pass, request.document_pass) && same(choice.document_wishes, request.document_wishes)
    && same(choice.knowledge_filter_model, request.knowledge_filter_model)
    && chosenModels.join(",") === [...(request.phase_models ?? [])].sort().join(",");
}

// The workflow a flow of `/api/phases` is, as the owner names it: by its medium, a PDF's with its mode
function describeWorkflowName(medium: string): string {
  if (medium.startsWith("pdf-")) {
    const mode = { "pdf-pages": "pages-as-images", "pdf-text-only": "text-only" }[medium] ?? medium.slice(4);
    return `PDF workflow · ${describePdfMode(mode).label}`;
  }
  return medium ? `${medium[0].toUpperCase()}${medium.slice(1)} workflow` : "";
}

// `pdfMode` is the mode the run form chose, "" for none: each PDF then takes the default its size picks
function describeSharedMedium(items: InboxItem[], pdfMode: string, defaults: RunDefaults | null): string {
  const media = new Set(items.map((item) => OFFICE_NAME.test(item.video) ? describePdfFlow("markdown-conversion")
    : SLIDES_NAME.test(item.video) ? describePdfFlow("pages-as-images")
    : item.video.toLowerCase().endsWith(".pdf")
    ? describePdfFlow(pdfMode || choosePdfMode(defaults, item.page_count) || "text-only")
    : item.is_audio ? "audio" : "video"));
  return media.size === 1 ? [...media][0] : "";
}

// `tickedItems` holds the ticked inbox rows and the ticked run folders alike (buildRunRow)
// `isAnyTicked` with none of them processable: only run folders naming no recording are ticked
function describeProcessButton(tickedItems: InboxItem[], newCount: number, isAnyTicked: boolean): string {
  const itemCount = tickedItems.length;
  if (itemCount) return `Process ${itemCount} ${describeTickedKind(tickedItems)}${itemCount === 1 ? "" : "s"}`;
  if (isAnyTicked) return "No element selected";
  return newCount ? `Process all new (${newCount})` : "Nothing new to process";
}

function describeRunState(run: RunSummary): InboxItem["state"] {
  if (run.is_live) return "running";
  return run.has_document || run.status === "complete" ? "done" : "started";
}

// How far a run went, for choosing one row among the run folders of one video.
const STATE_RANK: Record<string, number> = { running: 3, done: 2, started: 1, new: 0 };

// Run folders that name one video (a run begun again in a new folder) are one row, as a video in the inbox shows one
// state for all its runs: the run that went furthest, the newest among equals. An earlier attempt left behind never
// shows as a run of its own. A folder whose record names no video stands alone.
function pickLeadRuns(runs: RunSummary[]): RunSummary[] {
  const leads = new Map<string, RunSummary>();
  // a folder made from another run (Branch, Convert) is a run of its own, never an earlier attempt of its video
  const keyOf = (run: RunSummary) => (run.is_derived ? "" : run.video) || buildRunKey(run);
  for (const run of runs) {
    const key = keyOf(run);
    const lead = leads.get(key);
    const rank = STATE_RANK[describeRunState(run)];
    if (!lead || rank > STATE_RANK[describeRunState(lead)]
        || (rank === STATE_RANK[describeRunState(lead)] && run.state_at > lead.state_at)) {
      leads.set(key, run);
    }
  }
  return runs.filter((run) => leads.get(keyOf(run)) === run);
}

// The run a row opens, resets and resumes from: its live run, else its first; a new video has none
function leadRun(item: { runs: InboxItem["runs"] }): InboxItem["runs"][number] | undefined {
  return item.runs.find((candidate) => candidate.is_live) ?? item.runs[0];
}

// A run folder's key among the ticked rows, beside the videos' paths.
function buildRunKey(run: RunSummary): string {
  return `run:${buildRunPath(run)}`;
}

// A ticked run folder the inbox lists no recording for, as a row Process queues in that folder, as its own page's
// Resume does: a ticked done PDF run read "No video ticked", hid the PDF tab and queued nothing
function buildRunRow(run: RunSummary): InboxItem {
  const state = describeRunState(run);
  return { name: run.name, video: run.video, path: run.path, size_bytes: run.size_bytes ?? 0, run_folder: run.folder,
    output: run.folder, is_audio: run.is_audio, file_type: run.file_type, is_finished: state === "done",
    runs: [{ root: run.root, name: run.name, status: run.status, is_live: run.is_live, has_document: run.has_document,
      topic: run.topic, display_name: run.display_name, stopped_phase: run.stopped_phase, is_blocked: run.is_blocked }],
    parts: [], topic: run.topic, display_name: run.display_name, state, state_at: run.state_at };
}

// One list of the inbox: a header that opens and closes it, with a checkbox that ticks every video in it (a dash
// when only some are ticked), and its rows: its videos and its run folders together, by the status their pill shows
// (`rankPill`, done at the bottom), newest first by the date their state means (`state_at`) within one status.
// The key a dragged row carries, an inbox video's path or a run folder's key
export const ROW_DRAG_TYPE = "application/x-i2k-row";

function InboxSection({ kind, title, items, runs, ticked, isOpen, onToggleOpen, onSelectAll, renderItem, renderRun, onDropRow,
  onAdd, onUpload, onDownload, onRename, onRemove, classifyItemPill, classifyRunPill }: {
  kind: string;
  title: string;
  items: InboxItem[];
  runs: RunSummary[];
  // the class of the pill each row shows, which orders the rows
  classifyItemPill: (item: InboxItem) => string;
  classifyRunPill: (run: RunSummary) => string;
  ticked: Set<string>;
  isOpen: boolean;
  onToggleOpen: () => void;
  // every row of the list ticked or unticked; with `keys`, those rows, and the rows `others` names unticked
  onSelectAll: (isSelecting: boolean, keys?: string[], others?: string[]) => void;
  renderItem: (item: InboxItem) => ReactNode;
  renderRun: (run: RunSummary) => ReactNode;
  // the rows dropped on the section's head; a person's own list also opens its picker, renames and removes itself
  onDropRow?: (keys: string[]) => void;
  onAdd?: () => void;
  // a person's own list also uploads into the inbox and into itself (the owner: "list should have own upload button")
  onUpload?: () => void;
  // a person's own list holding done rows downloads their documents with their resources in one zip: the ticked done
  // rows, else every done row (the owner)
  // inactive while nothing of the list is ticked or a ticked row is not done; its title says why
  onDownload?: { title: string; run: () => void; isDisabled?: boolean };
  onRename?: (name: string) => void;
  onRemove?: () => void;
}) {
  const [isDropTarget, setIsDropTarget] = useState(false);
  const [nameDraft, setNameDraft] = useState<string | null>(null);
  // the one type of file the list shows, "" for every type (the owner: tick all videos of a list of PDFs and videos,
  // then process them together)
  const [shownType, setShownType] = useState("");
  const box = useRef<HTMLInputElement>(null);
  const allRows = [
    ...items.map((item) => ({ key: item.video, type: item.file_type || describeFileType(item.video), rank: rankPill(classifyItemPill(item)),
      at: item.state_at, render: () => renderItem(item) })),
    ...runs.map((run) => ({ key: buildRunKey(run), type: run.file_type || "", rank: rankPill(classifyRunPill(run)),
      at: run.state_at, render: () => renderRun(run) }))]
    .sort((one, other) => one.rank - other.rank || other.at - one.at);
  const rows = shownType ? allRows.filter((row) => row.type === shownType) : allRows;
  const keys = rows.map((row) => row.key);
  const tickedCount = allRows.filter((row) => ticked.has(row.key)).length;
  const isAllTicked = keys.length > 0 && keys.every((key) => ticked.has(key));
  // the types the list holds, with their counts, in a set order; the bar shows only when a row is ticked and the list
  // holds two types or more
  const typeCounts = FILE_TYPE_ORDER.map((type) => ({ type, count: allRows.filter((row) => row.type === type).length }))
    .filter((entry) => entry.count > 0);
  const isTypeBarShown = (tickedCount > 0 || shownType !== "") && typeCounts.length > 1;
  // choosing a type ticks nothing by itself; the head's box then ticks exactly the rows shown, and unticks the others of
  // this list, so a list's hidden PDFs are never processed with its videos
  const tickShown = (isTicking: boolean) => onSelectAll(isTicking, shownType ? keys : undefined,
    shownType ? allRows.filter((row) => row.type !== shownType).map((row) => row.key) : []);
  // a type chosen unticks the list's rows of the other types: hidden and still ticked, a list's PDFs were processed
  // with its videos (seen with the whole list ticked, then Videos chosen)
  const showType = (type: string) => {
    setShownType(type);
    if (type) onSelectAll(false, allRows.filter((row) => row.type !== type).map((row) => row.key));
  };

  useEffect(() => {
    if (box.current) box.current.indeterminate = tickedCount > 0 && !isAllTicked;
  }, [tickedCount, isAllTicked]);

  const renameTo = (name: string) => {
    setNameDraft(null);
    if (name.trim() && name.trim() !== title) onRename?.(name.trim());
  };

  return (
    <div className={`inbox-section section-${kind}${isDropTarget ? " drop-target" : ""}`}>
      <div className="section-head" aria-label={`${title}: drop a row here`}
        onDragOver={(event) => {
          if (!onDropRow || !event.dataTransfer.types.includes(ROW_DRAG_TYPE)) return;
          event.preventDefault();
          setIsDropTarget(true);
        }}
        onDragLeave={() => setIsDropTarget(false)}
        onDrop={(event) => {
          setIsDropTarget(false);
          const keys = event.dataTransfer.getData(ROW_DRAG_TYPE).split("\n").filter(Boolean);
          if (!onDropRow || !keys.length) return;
          event.preventDefault();
          onDropRow(keys);
        }}>
        <input ref={box} type="checkbox" checked={isAllTicked} disabled={!keys.length}
          aria-label={`select every video in ${title}`} onChange={() => tickShown(!isAllTicked)} />
        {nameDraft !== null ? (
          <input type="text" className="title-input" aria-label={`new name of ${title}`} value={nameDraft} autoFocus
            onChange={(event) => setNameDraft(event.target.value)} onBlur={() => renameTo(nameDraft)}
            onKeyDown={(event) => {
              if (event.key === "Enter") renameTo(nameDraft);
              if (event.key === "Escape") setNameDraft(null);
            }} />
        ) : (
          <button className="section-toggle" onClick={onToggleOpen} aria-expanded={isOpen}>
            <span>{title}</span>
            <span className="count">{allRows.length}</span>
            {tickedCount > 0 && <small className="muted">{tickedCount} ticked</small>}
            <span className={`caret${isOpen ? " open" : ""}`}><Icon name="chevron" size={14} /></span>
          </button>
        )}
        {onAdd && nameDraft === null && (
          <button className="btn ghost icon-only small" aria-label={`add rows to the list ${title}`}
            title="choose videos and runs to add to this list" onClick={onAdd}><Icon name="plus" size={14} /></button>
        )}
        {onUpload && nameDraft === null && (
          <button className="btn ghost icon-only small" aria-label={`upload into the list ${title}`}
            title="upload recordings into the inbox and this list" onClick={onUpload}><Icon name="upload" size={14} /></button>
        )}
        {onDownload && nameDraft === null && (
          <button className="btn ghost icon-only small" aria-label={`download from the list ${title}`}
            title={onDownload.title} disabled={onDownload.isDisabled} onClick={onDownload.run}>
            <Icon name="download" size={14} /></button>
        )}
        {onRename && nameDraft === null && (
          <button className="btn ghost icon-only small" aria-label={`rename the list ${title}`} title="rename this list"
            onClick={() => setNameDraft(title)}><Icon name="pencil" size={14} /></button>
        )}
        {/* an empty list alone can be deleted (the owner): its rows are dragged out onto a status section first */}
        {onRemove && nameDraft === null && allRows.length === 0 && (
          <button className="btn ghost icon-only small" aria-label={`delete the list ${title}`}
            title="delete this empty list" onClick={onRemove}>
            <Icon name="trash" size={14} />
          </button>
        )}
      </div>
      {isTypeBarShown && (
        <div className="type-bar" role="radiogroup" aria-label={`show one type in ${title}`}>
          <small className="muted">Show</small>
          {[{ type: "", count: allRows.length }, ...typeCounts].map((entry) => (
            <button key={entry.type || "all"} type="button" role="radio" aria-checked={shownType === entry.type}
              className={`chip small${shownType === entry.type ? " on" : ""}`}
              onClick={() => showType(entry.type)}>
              {FILE_TYPE_LABELS[entry.type] ?? entry.type} <small className="type-count">{entry.count}</small>
            </button>
          ))}
          {shownType && (
            <button type="button" className="btn ghost small tick-type"
              onClick={() => tickShown(!isAllTicked)}>
              {isAllTicked ? `Untick the ${keys.length}` : `Tick only the ${keys.length}`}
            </button>
          )}
        </div>
      )}
      {isOpen && <ul className="rows">{rows.map((row) => row.render())}</ul>}
    </div>
  );
}

// A started run's waiting job: "queued: continue" when it goes on from where the run stopped (a resume, or a start at
// a later phase), so it is told apart from a run queued to start over; "retrying" while it waits to try again.
export function labelWaitingJob(job: Job): string {
  if (job.status === "retrying") return "retrying";
  return job.request.is_resuming || job.request.from_phase ? "queued: continue" : "queued";
}

// A new video's pill: "queued" or "starting…" while a job of it waits or runs, before its run folder exists, and
// "run folder missing" when the job runs on with its folder gone (a deleted folder left the row starting for good).
// A started video sent to the queue again shows "queued", or "queued: continue" when its job goes on, until its job
// starts. A job's pill opens its console.
function NewItemPill({ job, name, onOpenJob, onOpenVideo, hasDocument = false }: { job: Job | undefined; name: string;
  onOpenJob: (job: Job) => void; onOpenVideo: () => void; hasDocument?: boolean }) {
  if (!job) {
    return (
      <button className="pill link new" title="open this video's page: play it, start its run" aria-label={`new › open the page of ${name}`}
        onClick={(event) => {
        event.preventDefault();
        onOpenVideo();
      }}>new ›</button>
    );
  }
  const label = job.is_output_missing ? "run folder missing" : job.status === "running" ? "starting…"
    : describeChangePill(job, hasDocument)?.label ?? labelWaitingJob(job);
  // A waiting or starting job opens the row's own page, which shows the video or what the run produced and links to the
  // job's console; opened as the console alone, a queued video showed neither (the owner). Only a job whose run folder
  // is gone opens its console, the one place that says why.
  const title = job.is_output_missing
    ? `job ${job.job_id} runs, but its run folder ${job.request.output_root} is gone: open its console`
    : `job ${job.job_id} waits: open this video's page`;

  return (
    <button className={`pill link ${job.is_output_missing ? "failed" : job.status}`} title={title}
      onClick={(event) => {
        event.preventDefault();
        if (job.is_output_missing) onOpenJob(job);
        else onOpenVideo();
      }}>
      <span className={`dot ${job.is_output_missing ? "failed" : job.status}`} /> {label} ›
    </button>
  );
}

// The status section a row stands in when no list of the person's holds it
const STATE_SECTION_TITLES: Record<InboxItem["state"], string> = {
  new: "New", running: "Running", started: "Not finished", done: "Done",
};

// The picker a person's list opens with its +: every video and run not in the list yet, grouped under the list or
// status section it stands in now, found by name and filtered by a tag; the ticked ones are added at once
function ListPicker({ list, lists, rows, onAdd, onClose }: {
  list: RunList;
  lists: RunList[];
  rows: { key: string; name: string; state: InboxItem["state"]; tags: string[] }[];
  onAdd: (keys: string[]) => void;
  onClose: () => void;
}) {
  const [search, setSearch] = useState("");
  const [tag, setTag] = useState<string | null>(null);
  // the tag chips stay folded until opened, and a search narrows them
  const [isTagsOpen, setIsTagsOpen] = useState(false);
  const [tagSearch, setTagSearch] = useState("");
  // the places the person opened; every place starts folded (the owner), and a name search or a tag opens them all
  const [openPlaces, setOpenPlaces] = useState<Set<string>>(new Set());
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const listOf = new Map(lists.flatMap((other) => other.members.map((member) => [member, other.name] as const)));
  const candidates = rows.filter((row) => !list.members.includes(row.key));
  const tags = [...new Set(candidates.flatMap((row) => row.tags))].sort((one, other) => one.localeCompare(other));
  const shown = candidates.filter((row) => isRunFound({ name: row.name }, search) && (!tag || row.tags.includes(tag)));
  // the status sections first, in the run list's order, then the person's other lists
  const places = [...Object.values(STATE_SECTION_TITLES), ...lists.filter((other) => other.id !== list.id).map((other) => other.name)];
  const placeOf = (row: { key: string; state: InboxItem["state"] }) => listOf.get(row.key) ?? STATE_SECTION_TITLES[row.state];
  const groups = places.map((place) => ({ place, rows: shown.filter((row) => placeOf(row) === place) }))
    .filter((group) => group.rows.length);
  const isPlaceOpen = (place: string) => Boolean(search.trim()) || tag !== null || openPlaces.has(place);

  return (
    <Dialog title={`Add to ${list.name}`} onClose={onClose}>
      {/* the name search and, beside it, the tag filter: a control over the rows, never a list of its own (the owner) */}
      <div className="inbox-search-row">
        <SearchBox label="find a video or a run" placeholder="find by name" value={search} onChange={setSearch} isFocused />
        {tags.length > 0 && (
          <button className={`btn ${tag ? "primary" : "ghost"} small list-picker-filter`} aria-expanded={isTagsOpen}
            aria-label="filter by tag" title="show only the rows with one tag" onClick={() => setIsTagsOpen(!isTagsOpen)}>
            <Icon name="filter" size={14} /> {tag ?? "Tag"}
          </button>
        )}
      </div>
      {tag && (
        <div className="list-picker-active">
          <span className="muted">Only rows tagged</span>
          <button className="chip small on" aria-label={`stop filtering by ${tag}`} onClick={() => setTag(null)}>
            {tag} <Icon name="x" size={12} />
          </button>
        </div>
      )}
      {isTagsOpen && (
        <div className="list-picker-filter-panel" role="group" aria-label="choose a tag to filter by">
          <SearchBox label="find a tag" placeholder={`find one of ${tags.length} tags`} value={tagSearch} onChange={setTagSearch} />
          <div className="type-filter">
            {tags.filter((one) => one.toLowerCase().includes(tagSearch.trim().toLowerCase())).map((one) => (
              <button key={one} className={`chip small${tag === one ? " on" : ""}`} aria-pressed={tag === one}
                onClick={() => {
                  setTag(tag === one ? null : one);
                  setIsTagsOpen(false);
                }}>{one}</button>
            ))}
          </div>
        </div>
      )}
      {groups.length ? (
        <div className="list-picker">
          {groups.map((group) => {
            const pickedCount = group.rows.filter((row) => picked.has(row.key)).length;
            const isAllPicked = pickedCount === group.rows.length;
            return (
            <section key={group.place} aria-label={group.place}>
              <div className="list-picker-head">
                {/* ticks every row of the place the search and the tag show; a dash when only some are ticked */}
                <input type="checkbox" checked={isAllPicked} aria-label={`pick every row in ${group.place}`}
                  ref={(box) => {
                    if (box) box.indeterminate = pickedCount > 0 && !isAllPicked;
                  }}
                  onChange={() => setPicked((previous) => {
                    const next = new Set(previous);
                    group.rows.forEach((row) => (isAllPicked ? next.delete(row.key) : next.add(row.key)));
                    return next;
                  })} />
                <button className="section-toggle list-picker-place" aria-expanded={isPlaceOpen(group.place)}
                  onClick={() => setOpenPlaces((previous) => toggleInSet(previous, group.place))}>
                  <span>{group.place}</span>
                  <span className="count">{group.rows.length}</span>
                  {pickedCount > 0 && <small className="muted">{pickedCount} picked</small>}
                  <span className={`caret${isPlaceOpen(group.place) ? " open" : ""}`}><Icon name="chevron" size={14} /></span>
                </button>
              </div>
              {isPlaceOpen(group.place) && <ul>
                {group.rows.map((row) => (
                  <li key={row.key}>
                    <label className="row selectable">
                      <input type="checkbox" checked={picked.has(row.key)} aria-label={`pick ${row.name}`}
                        onChange={() => setPicked((previous) => toggleInSet(previous, row.key))} />
                      <span className="truncate" title={row.name}>{row.name}</span>
                      <span className={`pill ${STATE_PILL_CLASSES[row.state]}`}>{formatRunState(row.state)}</span>
                    </label>
                  </li>
                ))}
              </ul>}
            </section>
            );
          })}
        </div>
      ) : (
        <p className="empty-row">{candidates.length ? "Nothing matches the search and the tag." : "Every video and run is in this list already."}</p>
      )}
      <div className="dialog-actions">
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={!picked.size} onClick={() => onAdd([...picked])}>
          <Icon name="plus" /> Add {picked.size || ""}
        </button>
      </div>
    </Dialog>
  );
}

// The inbox: drop recordings to upload them, tick the ones to process, choose the phases, and queue them.
export default function InboxPanel({
  items,
  isLoading = false,
  phases,
  runs,
  jobs,
  openKey,
  onOpenRun,
  onOpenJob,
  onOpenVideo,
  onChanged,
  onMessage,
}: {
  items: InboxItem[];
  // the lists have not arrived yet: say so, never that the inbox is empty
  isLoading?: boolean;
  phases: Phase[];
  runs: RunSummary[];
  // the queue's jobs: a video sent to it shows as queued or starting until its run folder says more
  jobs: Job[];
  openKey: string;
  onOpenRun: (root: string, name: string) => void;
  onOpenJob: (job: Job) => void;
  // a video no run used yet: its page, where it plays and its run starts
  onOpenVideo: (item: InboxItem) => void;
  onChanged: () => void;
  onMessage: (text: string, isError?: boolean) => void;
}) {
  // the videos' paths and the run folders' keys (buildRunKey) ticked in the lists
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  // The person's own lists, beside the status sections
  const [runLists, setRunLists] = useState<RunList[]>([]);
  const [newListName, setNewListName] = useState<string | null>(null);
  useEffect(() => {
    fetchRunLists().then(setRunLists).catch(() => setRunLists([]));
  }, []);
  const saveLists = (next: RunList[]) => {
    setRunLists(next);
    saveRunLists(next).then(setRunLists).catch((error) => onMessage(describeError(error), true));
  };
  // the list whose + opened the picker of rows to add to it
  const [pickingFor, setPickingFor] = useState<RunList | null>(null);
  // the upload's picker, opened by a list's upload button; the names it saved wait here until the inbox lists them,
  // then go into the list
  const uploadHandle = useRef<UploadHandle | null>(null);
  const [savedForList, setSavedForList] = useState<{ listId: string; names: string[] }[]>([]);
  // Moves rows into a list (none: back to their status sections), and unticks them, so the next drag starts afresh
  // A list's download: the documents of its ticked rows with their resources, in one zip; always shown, active only
  // while rows of the list are ticked and every ticked one is done (the owner: "instead of hiding it, make it inactive")
  const describeListDownload = (section: { title: string; items: InboxItem[]; runs: RunSummary[] }) => {
    const tickedKeys = [...section.items.map((item) => item.video), ...section.runs.map(buildRunKey)]
      .filter((key) => ticked.has(key));
    const doneRows = [
      ...section.items.filter((item) => item.state === "done").map((item) => ({ key: item.video,
        run: item.runs.find((run) => run.has_document) ?? leadRun(item) })),
      ...section.runs.filter((run) => describeRunState(run) === "done").map((run) => ({ key: buildRunKey(run), run })),
    ].filter((row) => row.run !== undefined) as { key: string; run: { root: string; name: string } }[];
    const chosen = doneRows.filter((row) => ticked.has(row.key));
    if (!tickedKeys.length) return { title: "tick done documents of this list to download them", run: () => {}, isDisabled: true };
    if (chosen.length !== tickedKeys.length) {
      return { title: "every ticked row must be done to download: untick the ones not done", run: () => {}, isDisabled: true };
    }
    const refs = chosen.map((row) => ({ root: row.run.root, name: row.run.name }));
    return {
      title: `download the ticked done document${chosen.length === 1 ? "" : "s"} (${chosen.length}) with their figures, as a zip`,
      // a run with several versions of its document (a clean up, a rewrite, a knowledge reshaping) asks which ones
      // first; with one version each there is nothing to ask (the owner: "if one of the selected object has multiple
      // final document it should show a panel ask me which version")
      run: () => {
        fetchDocumentVersions(refs)
          .then((found) => {
            const several = found.filter((run) => run.versions.length > 1);
            if (several.length) setVersionAsk({ refs, title: section.title, several });
            else void downloadVersion(refs, section.title, "");
          })
          .catch((error) => onMessage(describeError(error), true));
      },
    };
  };
  const downloadVersion = async (refs: { root: string; name: string }[], title: string, version: DownloadVersion) => {
    setVersionAsk(null);
    try {
      const skipped = await downloadDocuments(refs, ["document"], version);
      onMessage(skipped.length ? `Downloaded; no document in: ${skipped.join(", ")}`
        : `Downloaded ${refs.length} document${refs.length === 1 ? "" : "s"} from ${title}`, skipped.length > 0);
    } catch (error) {
      onMessage(describeError(error), true);
    }
  };
  const moveRowsToList = (keys: string[], listId: string | null) => {
    // a run folder a list holds by the inbox path its recording came from moves with that path too, or it stays listed
    const inboxPaths = new Set(items.map((item) => item.video));
    const recordingPaths = runs.filter((run) => keys.includes(buildRunKey(run)) && run.video && !inboxPaths.has(run.video))
      .map((run) => run.video);
    saveLists(moveToList(runLists, [...new Set([...keys, ...recordingPaths])], listId));
    setTicked((previous) => new Set([...previous].filter((key) => !keys.includes(key))));
  };
  // A ticked row dragged carries every ticked row with it (the owner ticked several and only the one held moved);
  // an unticked one goes alone
  const startRowDrag = (event: DragEvent, key: string) =>
    event.dataTransfer.setData(ROW_DRAG_TYPE, (ticked.has(key) ? [...ticked] : [key]).join("\n"));
  const createList = (name: string) => {
    setNewListName(null);
    if (!name.trim()) return;
    saveLists([...runLists, { id: `list-${Date.now().toString(36)}`, name: name.trim(), members: [] }]);
  };
  // Files uploaded through a list's button go into that list once the inbox lists them under their saved names
  useEffect(() => {
    if (!savedForList.length) return;
    const waiting = savedForList.map(({ listId, names }) => {
      const found = items.filter((item) => names.includes(item.name));
      if (found.length) moveRowsToList(found.map((item) => item.video), listId);
      return { listId, names: names.filter((name) => !found.some((item) => item.name === name)) };
    });
    if (waiting.some((entry, index) => entry.names.length !== savedForList[index].names.length)) {
      setSavedForList(waiting.filter((entry) => entry.names.length));
    }
  }, [items, savedForList]);
  // what the inbox's search box holds
  const [search, setSearch] = useState("");
  // the type bar under the search: every row, or only the videos, audio files or PDFs
  const [fileType, setFileType] = useState("all");
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  // the panel a file can be dropped anywhere on
  const panelRef = useRef<HTMLDivElement>(null);
  const closeSearch = () => {
    setIsSearchOpen(false);
    setSearch("");
    setFileType("all");
  };
  // the settings profile and the video workflow chosen here are kept, so a video's own page starts from them (the
  // owner chose the fast settings here and the video's page read "Default: Quality")
  const [savedRunChoice, setSavedRunChoice] = usePersistentState<RunSettingsChoice>(RUN_SETTINGS_CHOICE_KEY,
    { profile: "" });
  const [selection, setSelectionState] = useState<PhaseSelection>({ ...NO_SELECTION,
    ...(savedRunChoice.profile ? { profile: savedRunChoice.profile } : {}),
    ...(savedRunChoice.speed_preset ? { speed_preset: savedRunChoice.speed_preset,
      speed_settings: savedRunChoice.speed_settings ?? {} } : {}) });
  const setSelection = (next: PhaseSelection) => {
    setSelectionState(next);
    setSavedRunChoice({ profile: next.profile ?? "", speed_preset: next.speed_preset ?? "",
      speed_settings: next.speed_settings ?? {} });
  };
  const [isRunSettingsOpen, setIsRunSettingsOpen] = useState(false);
  // the recordings Process was asked for while one of them is done, waiting on the warning's answer
  const [redoAsked, setRedoAsked] = useState<InboxItem[] | null>(null);
  // a list's download whose runs hold several versions of their document, waiting for which version to download
  const [versionAsk, setVersionAsk] = useState<{ refs: { root: string; name: string }[]; title: string;
    several: RunDocumentVersions[] } | null>(null);
  const [archive, setArchive] = useState<ArchiveEntry[]>([]);
  // bumped after rows move to the trash, so the trash reads itself again
  const [trashMoves, setTrashMoves] = useState(0);
  // the rows "Move to trash?" asks about, and how many of them are not done (the dialog's "This file" or "These files")
  const [trashWarning, setTrashWarning] = useState<{ items: InboxItem[]; runs: RunSummary[]; unfinishedCount: number } | null>(null);
  // Reset to phase over every ticked run: the phase to keep up to while its dialog is open, and whether it is working
  const [resetPhase, setResetPhase] = useState<number | null>(null);
  // the merge of the ticked recordings into one, open in its dialog
  const [isMergeOpen, setIsMergeOpen] = useState(false);
  const [isResetting, setIsResetting] = useState(false);
  const [renaming, setRenaming] = useState<InboxItem | null>(null);
  const [nameDraft, setNameDraft] = useState("");
  const [renameError, setRenameError] = useState("");

  const startRename = (item: InboxItem) => {
    setRenaming(item);
    setNameDraft(item.name);
    setRenameError("");
  };

  const saveRename = async () => {
    if (!renaming) return;
    try {
      const answer = await renameInboxVideo(renaming.video, nameDraft);
      onMessage(`Renamed ${renaming.name} to ${answer.name}`);
      setRenaming(null);
      setTicked((previous) => {
        const next = new Set(previous);
        next.delete(renaming.video);
        return next;
      });
      onChanged();
    } catch (error) {
      setRenameError(describeError(error));
    }
  };
  const [openKeys, setOpenKeys] = usePersistentState<string[]>("inboxSections", ["new", "running", "unfinished"]);
  const openSections = new Set(openKeys);
  // Which sections are open is kept in the view cookie, so a refresh shows the same ones.
  const toggleSection = (key: string) =>
    setOpenKeys((previous) => (previous.includes(key) ? previous.filter((open) => open !== key) : [...previous, key]));

  // An archive that cannot be read keeps what it showed and says why, never an empty list that reads as "nothing there"
  const reloadArchive = () => fetchJson<ArchiveEntry[]>("/inbox/archive").then(setArchive)
    .catch((error) => onMessage(`The archive could not be read: ${describeError(error)}`, true));
  // The archive and the trash are read again when the inbox gains or loses a video: a recording moved aside here, in
  // another tab or by `i2k` leaves the inbox, and that is the one change the 3-second inbox poll shows of it
  useEffect(() => {
    void reloadArchive();
    setTrashMoves((count) => count + 1);
  }, [items.length]);

  // Moves the ticked rows aside: videos' recordings only (their run folders stay listed), a run folder no inbox video
  // owns whole. An archived video leaves the lists with its runs, listed under Archive; a trashed one keeps its runs here.
  const moveTicked = async (place: InboxPlace, rows: InboxItem[], runRows: RunSummary[]) => {
    try {
      const outcomes = await moveRows(place, rows.map((item) => item.video), runRows.map(buildRunPath));
      const moved = place === "archive"
        ? `Archived ${outcomes.length}: listed under Archive with ${outcomes.length === 1 ? "its" : "their"} runs`
        : `Moved ${outcomes.length} to inbox/.trash.${rows.length ? " Their runs stay listed in the inbox." : ""}`;
      const outcome = describeMoveOutcomes(outcomes, moved);
      onMessage(outcome.text, outcome.isError);
      setTicked(new Set());
      onChanged();
      if (place === "archive") void reloadArchive();
      else setTrashMoves((count) => count + 1);
    } catch (error) {
      onMessage(describeError(error), true);
    }
  };

  // Unarchive brings an entry back where it came from, under the name it had
  const unarchive = async (name: string) => {
    try {
      const outcome = await moveBack("archive", name);
      const isRunFolder = Boolean(archive.find((entry) => entry.name === name)?.run_folder);
      onMessage(outcome.error || `Moved ${outcome.restored_as} back ${isRunFolder ? "to its run folders" : "into the inbox"}`,
        Boolean(outcome.error));
      onChanged();
      void reloadArchive();
    } catch (error) {
      onMessage(describeError(error), true);
    }
  };

  // Each ticked run folder is reset in turn by the detail view's own `reset-to`; one the server refuses (a live run)
  // is named in the message and the others still go.
  const resetRuns = async (targets: { root: string; name: string }[], phase: number) => {
    setIsResetting(true);
    const failures: string[] = [];
    for (const target of targets) {
      try {
        const answer = await sendJson<CommandOutput>(`${buildRunBase(target)}/reset`, { kind: "phase", number: phase, is_confirmed: true });
        const { refusal } = readResetOutput(answer);
        if (refusal) failures.push(`${target.name}: ${refusal}`);
      } catch (error) {
        failures.push(`${target.name}: ${describeError(error)}`);
      }
    }
    setIsResetting(false);
    setResetPhase(null);
    const doneCount = targets.length - failures.length;
    onMessage([doneCount ? `Reset ${doneCount} run${doneCount === 1 ? "" : "s"} to phase ${phase}` : "", ...failures]
      .filter(Boolean).join("; "), failures.length > 0);
    setTicked(new Set());
    onChanged();
  };

  // Rows not done are named in a warning first; done ones go at once
  const askToTrash = (rows: InboxItem[], runRows: RunSummary[]) => {
    const unfinishedCount = rows.filter((item) => item.state !== "done").length
      + runRows.filter((run) => describeRunState(run) !== "done").length;
    if (unfinishedCount) setTrashWarning({ items: rows, runs: runRows, unfinishedCount });
    else void moveTicked("trash", rows, runRows);
  };

  // The settings profiles that exist, so a resume never names one whose file is gone, and the models the Phases panel
  // offers: read once here, since the panel is drawn afresh each time its menu opens
  const settingsProfiles = useSettingsProfiles();
  const { phaseModels } = usePhaseModels();

  // The ticked done PDFs' documents changed by an LLM pass, as phase 7 alone in each one's own run folder: nothing runs
  // from the start (the owner: "change the final document in batches ... i dont want to run selected pdf from the start")
  const changeDocuments = async (rows: InboxItem[], pass: DocumentPass, wishes: string, source: DocumentSource) => {
    setIsRunSettingsOpen(false);
    try {
      await queueJobs(rows.map((row) => ({ video: row.video, output: row.output })), { ...NO_SELECTION, only_phase: 7,
        is_resuming: true, document_pass: pass, document_wishes: wishes, document_source: source });
    } catch (error) {
      onMessage(`The ${describeDocumentPass(pass)} was refused: ${describeError(error)}`, true);
      return;
    }
    onMessage(`Queued a ${describeDocumentPass(pass)} of ${rows.length} document${rows.length === 1 ? "" : "s"}`);
    setTicked(new Set());
  };

  const process = async (asked: InboxItem[], isRedoConfirmed = false) => {
    // a video whose job waits or starts is never queued twice, from its row or from the menu
    const chosen = asked.filter((item) => !findPendingJob(item));
    if (!chosen.length) {
      onMessage("Nothing to process: tick a video, or every inbox video already has a run.", true);
      return;
    }
    // A done recording is processed again from its first phase in its own run folder, its document written over: asked
    // first, with the choice to leave the done ones out (the owner)
    if (!isRedoConfirmed && chosen.some((item) => item.state === "done")) {
      setRedoAsked(chosen);
      return;
    }
    // Told "queued" with no run going, the user could not tell whether to wait or click again
    const ahead = countJobsAhead(jobs);
    // A started video's run is resumed when every phase is chosen, as its ▶ says: run without a resume it would run
    // every phase again over what the run already wrote
    const isEveryPhase = selection.only_phase === null && selection.from_phase === null && !selection.worker_indexes.length;
    const isResumed = (item: InboxItem) => isEveryPhase && item.state === "started";
    // A resume keeps the settings profile its run was made with, while that profile exists (a run line naming a
    // missing one is refused); a profile chosen in the Phases menu wins
    const resumeProfileOf = (item: InboxItem) => {
      const lead = leadRun(item);
      const profile = runs.find((run) => run.root === lead?.root && run.name === lead?.name)?.settings_profile ?? "";
      return selection.profile || (isProfileGone(profile, settingsProfiles.profiles) ? "" : profile);
    };
    // Each video goes with its own choices: resumed or not, its resume's profile, and the tags given on its own page
    // beside the ones chosen here; videos with the same choices share one request
    // The PDF choices go with the PDFs alone: on a video's run line `--pdf-mode` would be written into its folder
    const choiceOf = (item: InboxItem): PhaseSelection => {
      const tags = mergeTags(selection.tags, item.tags ?? []);
      const { pdf_mode: _mode, pdf_ocr_engine: _engine, document_pass: _pass,
        document_wishes: _wishes, document_source: _source, ...withoutPdf } = selection;
      const base = isPdfItem(item) ? selection : withoutPdf;
      return isResumed(item) ? { ...base, is_resuming: true, profile: resumeProfileOf(item), tags } : { ...base, tags };
    };
    const grouped = new Map<string, [InboxItem[], PhaseSelection]>();
    // A recording whose run goes or waits is queued again only with other run settings than that run's: with the same
    // ones it is left out and named (the owner: ticked with the others while running, it was queued a second time)
    const alreadyRunning: string[] = [];
    for (const item of chosen) {
      const choice = choiceOf(item);
      const live = jobs.find((job) => (job.status === "running" || job.status === "queued") && job.request.video === item.video);
      if (live && isSameRunChoice(choice, live.request)) {
        alreadyRunning.push(item.name);
        continue;
      }
      const key = JSON.stringify(choice);
      grouped.set(key, [[...(grouped.get(key)?.[0] ?? []), item], choice]);
    }
    // Each request queues its group or is refused alone: what went is named, unticked and shown, so a second click
    // never queues it again, and what was refused stays ticked with the server's reason
    const queued: InboxItem[] = [];
    let refusal = "";
    for (const [group, choice] of grouped.values()) {
      try {
        await queueJobs(group.map((item) => ({ video: item.video, output: item.output })), choice);
        queued.push(...group);
      } catch (error) {
        refusal = `${group.map((item) => item.name).join(", ")} refused: ${describeError(error)}`;
        break;
      }
    }
    const names = queued.map((item) => item.name);
    const leftOut = alreadyRunning.length
      ? `${alreadyRunning.join(", ")} left out: already running with the same settings` : "";
    if (refusal) onMessage([names.length ? `Queued ${names.join(", ")}` : "", refusal, leftOut].filter(Boolean).join("; "), true);
    else if (!names.length) onMessage(leftOut, true);
    else onMessage([describeQueued(names, ahead), leftOut].filter(Boolean).join("; "));
    if (!queued.length) return;
    setTicked((previous) => {
      const next = new Set(previous);
      queued.forEach((item) => next.delete(item.video));
      // a ticked run folder is ticked by its own key (buildRunRow)
      otherRuns.filter((run) => queued.some((item) => item.output === run.folder))
        .forEach((run) => next.delete(buildRunKey(run)));
      return next;
    });
    onChanged();
  };


  // The reset opens keeping what every ticked run holds whole: the phase before the earliest one a run stopped in (the
  // server names it from the same statuses as each run's own page)
  const keptWholePhase = () => {
    const stops = [...tickedItems.map((item) => leadRun(item)?.stopped_phase ?? 0), ...tickedRuns.map((run) => run.stopped_phase ?? 0)]
      .filter((phase) => phase > 0);
    return stops.length ? Math.max(0, Math.min(...stops) - 1) : resetMax;
  };

  // a video's path or a run folder's key
  const toggle = (key: string) => setTicked((previous) => toggleInSet(previous, key));
  // Four sections that open and close: New, Running, Not finished and Done.
  // Run folders no inbox video owns (their recording went to the trash, or the run was typed by hand from elsewhere)
  // sit in the section their state belongs to, as rows that open the run; several of one video are one row.
  const ownedRuns = new Set([...items, ...archive].flatMap((item) => [...item.runs, ...("empty_runs" in item ? item.empty_runs ?? [] : [])]
    .map(buildRunPath)));
  const otherRuns = pickLeadRuns(runs.filter((run) => !ownedRuns.has(buildRunPath(run))));
  // The search finds a video or a run by its name, its AI name or its topic, whatever section it stands in (the owner:
  // "I don't always know where it is"); every section holding a match opens while it is typed
  const needle = search.trim();
  const isFound = (entry: { name: string; display_name?: string; topic?: string; file_type?: string }) =>
    isRunFound(entry, search) && (fileType === "all" || entry.file_type === fileType);
  // A row in one of the person's lists stands there, and leaves its status section until it is done: a done row stands
  // in Done too, and stays in its list (the owner: done is shown for every row, a list keeps what was put in it)
  const listed = new Set(runLists.flatMap((list) => list.members));
  // A list holds a video by its inbox path; once its run is done the recording moves into the run folder and the inbox
  // lists it no more, so the list holds its run folders by the path their recording came from (the owner: a PDF of a
  // list was gone from it once done)
  const inboxVideos = new Set(items.map((item) => item.video));
  const isMember = (members: Set<string> | string[], run: RunSummary) => {
    const has = (key: string) => (Array.isArray(members) ? members.includes(key) : members.has(key));
    // a run started again from the recording it keeps names that copy as its video: its list holds the inbox path the
    // copy came from (the owner: the running run left Review later for Running)
    return has(buildRunKey(run)) || (Boolean(run.video) && !inboxVideos.has(run.video) && has(run.video))
      || (Boolean(run.origin_video) && !inboxVideos.has(run.origin_video ?? "") && has(run.origin_video ?? ""));
  };
  const statusItems = items.filter((item) => !listed.has(item.video));
  const statusRuns = otherRuns.filter((run) => !isMember(listed, run));
  const statusSections = [
    { key: "new", title: "New", items: statusItems.filter((item) => item.state === "new"), runs: [] as RunSummary[] },
    { key: "running", title: "Running", items: statusItems.filter((item) => item.state === "running"),
      runs: statusRuns.filter((run) => describeRunState(run) === "running") },
    { key: "unfinished", title: "Not finished", items: statusItems.filter((item) => item.state === "started"),
      runs: statusRuns.filter((run) => describeRunState(run) === "started") },
    { key: "done", title: "Done", items: items.filter((item) => item.state === "done"),
      runs: otherRuns.filter((run) => describeRunState(run) === "done") },
  ].map((section) => ({ ...section, listId: null as string | null }));
  const listSections = runLists.map((list) => ({
    key: `list-${list.id}`, title: list.name, listId: list.id as string | null,
    items: items.filter((item) => list.members.includes(item.video)),
    runs: otherRuns.filter((run) => isMember(list.members, run)),
  }));
  // a person's list stays shown while empty, so a row can be dropped on it; a status section only while it holds one
  const sections = [...statusSections, ...listSections]
    .map((section) => ({ ...section, items: section.items.filter(isFound), runs: section.runs.filter(isFound) }))
    .filter((section) => section.listId !== null || section.items.length + section.runs.length > 0);
  // the Archive, like the sections, shows only what the search finds and opens while it is typed
  const foundArchive = archive.filter(isFound);
  const isArchiveOpen = Boolean(needle) || openSections.has("archive");
  // A video's job that waits or has started while the inbox still reads its folder as new, or that waits in the queue
  // to resume a run already started (a resumed video read "started" with no sign it was queued)
  const pendingJobs = new Map(jobs.filter((job) => job.status === "queued" || job.status === "running")
    .map((job) => [job.request.video, job] as const));
  const findPendingJob = (item: InboxItem) => {
    const job = pendingJobs.get(item.video);
    return item.state === "new" || job?.status === "queued" ? job : undefined;
  };
  // the last job of each run folder and each video, later ones replacing earlier ones, for the row's last state
  const lastJobsOfFolders = new Map(jobs.map((job) => [job.request.output_root, job] as const));
  const lastJobsOfVideos = new Map(jobs.map((job) => [job.request.video, job] as const));
  // a running pill names its run's provider set only while the running runs use more than one set, so the owner sees
  // which machine runs which recording; with one set the name tells nothing
  const isSeveralSetsRunning = new Set(runs.filter((run) => run.is_live).map((run) => run.provider_set || "default")).size > 1;
  const nameRunningSet = (run: { provider_set?: string }) => (isSeveralSetsRunning ? run.provider_set || "default" : "");
  // the class of the pill a row shows, as its row renders it below, which also orders the rows of each section
  const classifyItemPill = (item: InboxItem) => {
    const lead = leadRun(item);
    const pendingJob = findPendingJob(item);
    if (lead && !pendingJob) {
      return describeChangePill(lastJobsOfVideos.get(item.video), lead.has_document, lead.status)?.pillClass
        ?? classifyStatePill(item.state, lead.is_blocked);
    }
    if (!pendingJob) return "new";
    return pendingJob.is_output_missing ? "failed" : pendingJob.status;
  };
  // a run row whose folder has a job waiting in the queue says so, as a new video's row does: a blocked run queued
  // again said "blocked in phase 3" while its own page said queued (the owner)
  const findWaitingJobOfRun = (run: RunSummary) => {
    const job = lastJobsOfFolders.get(run.folder);
    return !run.is_live && (job?.status === "queued" || job?.status === "retrying") ? job : undefined;
  };
  const classifyRunPill = (run: RunSummary) =>
    findWaitingJobOfRun(run)?.status
      ?? describeChangePill(lastJobsOfFolders.get(run.folder), run.has_document, run.status)?.pillClass
      ?? classifyStatePill(describeRunState(run), run.is_blocked);
  // what the ⚙ arrow is set to, shown on the Process button when it is not the default
  const runSettings = describeSelection(selection, phaseModels);
  const newItems = items.filter((item) => item.state === "new" && !findPendingJob(item));
  const tickedItems = items.filter((item) => ticked.has(item.video));
  const tickedRuns = otherRuns.filter((run) => ticked.has(buildRunKey(run)));
  // why the merge button cannot merge what is ticked, "" when it can: two or more new single recordings, all video or
  // all audio, no PDF, no run folder
  let mergeRefusal = "";
  if (tickedItems.length < 2 || tickedRuns.length) mergeRefusal = "tick two or more recordings to merge them into one";
  else if (tickedItems.some((item) => item.state !== "new" || findPendingJob(item))) {
    mergeRefusal = "only recordings no run has used yet are merged";
  } else if (tickedItems.some((item) => item.parts.length > 0)) mergeRefusal = "a recording in parts is not merged again";
  else if (tickedItems.some(isPdfItem)) mergeRefusal = "a PDF is not merged: only videos or audio recordings";
  else if (new Set(tickedItems.map((item) => item.is_audio)).size > 1) {
    mergeRefusal = "a video and an audio recording are not merged together";
  }
  // every ticked row Process queues: the inbox's recordings, then the run folders whose recording the inbox lists not
  const tickedRows = [...tickedItems, ...tickedRuns.filter((run) => run.video).map(buildRunRow)];
  // what Process queues: the ticked rows, else every new recording while nothing is ticked
  const isAnyTicked = Boolean(tickedItems.length || tickedRuns.length);
  // the ticked PDFs with a finished document, which an LLM pass can change without running anything else again
  const changeableRows = tickedRows.filter((row) => isPdfItem(row) && row.runs.some((run) => run.has_document));
  // a pass one of them already had, so the change asks whether to start from the original or that pass's document
  const changedPass = changeableRows.map((row) => leadRun(row)).map((lead) => runs.find((run) => run.root === lead?.root
    && run.name === lead?.name)?.document_pass ?? "").find(Boolean) ?? "";
  const processedRows = isAnyTicked ? tickedRows : newItems;
  const processLabel = describeProcessButton(tickedRows, newItems.length, isAnyTicked);
  // The ⚙ arrow's phases for the medium every ticked recording shares (a PDF's titled by its pages or figures, with no
  // review or critique collection when read as text only, an audio recording's likewise), else the video's: a ticked
  // PDF was offered "transcribe" and the video's phases
  const runDefaults = useRunDefaults(selection.profile);
  const tickedMedium = describeSharedMedium(tickedRows, selection.pdf_mode ?? "", runDefaults);
  const [mediumPhases, setMediumPhases] = useState<Phase[] | null>(null);
  // phase 7 shows only while a PDF's clean up or rewrite is chosen
  const tickedPass = tickedMedium.startsWith("pdf-") ? selection.document_pass ?? "" : "";
  // the phases the chosen speed switches off (Balanced has no review and no critique collection), a video's only
  const speedOffKey = JSON.stringify(readSpeedOffPhases(selection, runDefaults));
  useEffect(() => {
    setMediumPhases(null);
    if (!tickedMedium) return;
    const speedOff = tickedMedium === "video" ? JSON.parse(speedOffKey) : {};
    fetchJson<Phase[]>(`/phases?${new URLSearchParams({ medium: tickedMedium,
      ...(tickedPass ? { document_pass: tickedPass } : {}), ...speedOff })}`)
      .then(setMediumPhases).catch(() => setMediumPhases(null));
  }, [tickedMedium, tickedPass, speedOffKey]);
  // Each workflow the recordings Process would queue take (the ticked ones, else every new one), with its phases: the
  // models tab shows a section each, so a model is chosen knowing which workflow's phase it is (the owner)
  const targetMedia = [...new Set((tickedRows.length ? tickedRows : newItems)
    .map((item) => describeSharedMedium([item], selection.pdf_mode ?? "", runDefaults)))].sort().join(",");
  const [workflows, setWorkflows] = useState<{ name: string; phases: Phase[] }[]>([]);
  useEffect(() => {
    if (!isRunSettingsOpen || !targetMedia) {
      setWorkflows([]);
      return;
    }
    Promise.all(targetMedia.split(",").map((medium) =>
      fetchJson<Phase[]>(`/phases?${new URLSearchParams({ medium,
        ...(medium === "video" ? JSON.parse(speedOffKey) : {}) })}`)
        .then((flowPhases) => ({ name: describeWorkflowName(medium), phases: flowPhases }))))
      .then(setWorkflows).catch(() => setWorkflows([]));
  }, [targetMedia, isRunSettingsOpen, speedOffKey]);
  // The run folders a reset of the ticked rows takes back: each ticked video's run its name opens, and each ticked run
  // folder; a new video has none
  const resetTargets: { root: string; name: string }[] = [
    ...tickedItems.flatMap((item) => {
      const run = leadRun(item);
      return run ? [run] : [];
    }),
    ...tickedRuns.map((run) => ({ root: run.root, name: run.name })),
  ];
  // `reset-to` keeps phases up to any one before the last
  const resetMax = Math.max(0, ...phases.map((phase) => phase.number)) - 1;

  return (
    <div className="tab-body" ref={panelRef}>
      {/* The upload by default, the search and the type filter behind 🔍 in its place: both at once took the room the
          list needs (the owner); closing the search clears it, so no hidden filter is left on the list */}
      <InboxUpload onChanged={onChanged} onMessage={onMessage} onOpenRun={onOpenRun} panel={panelRef} handle={uploadHandle}
        onSaved={(names, listId) => setSavedForList((previous) => [...previous, { listId, names }])}
        isZoneShown={!isSearchOpen} beside={(items.length > 0 || otherRuns.length > 0) && (
          <button className="btn icon-only search-toggle" aria-label="search and filter"
            title="find a video or a run, or show only one type" onClick={() => setIsSearchOpen(true)}>
            <Icon name="search" />
          </button>
        )} />
      {isSearchOpen && (
        <div className="inbox-search">
          <div className="inbox-search-row">
            <SearchBox label="find a video or a run" placeholder="find a video or a run by name or topic" value={search}
              onChange={setSearch} isFocused />
            <button className="btn icon-only search-toggle" aria-label="close the search"
              title="close the search and show the upload" onClick={closeSearch}><Icon name="upload" /></button>
          </div>
          <div className="type-filter" role="group" aria-label="show only one type">
            {FILE_TYPE_FILTERS.map((type) => (
              <button key={type} className={`chip small${fileType === type ? " on" : ""}`} aria-pressed={fileType === type}
                onClick={() => setFileType(type)}>{type}</button>
            ))}
          </div>
        </div>
      )}

      {isLoading && <Spinner />}
      {(needle || fileType !== "all") && sections.length === 0 && !foundArchive.length && (
        <p className="empty-row">
          Nothing in the inbox{fileType !== "all" ? ` is a ${fileType}` : ""}{needle ? `${fileType !== "all" ? " and" : ""} matches "${needle}"` : ""}.
        </p>
      )}
      {!isLoading && items.length === 0 && <p className="empty-row">The inbox is empty. Drop a recording above.</p>}
      {sections.map((section) => (
        <InboxSection key={section.key} kind={section.listId ? "list" : section.key} title={section.title} items={section.items}
          ticked={ticked} runs={section.runs} classifyItemPill={classifyItemPill} classifyRunPill={classifyRunPill}
          onDropRow={(keys) => moveRowsToList(keys, section.listId)}
          onAdd={section.listId ? () => setPickingFor(runLists.find((list) => list.id === section.listId) ?? null) : undefined}
          onUpload={section.listId ? () => uploadHandle.current?.pick(section.listId!) : undefined}
          // only where a row can be done: Done and the person's own lists; New and Not finished never hold one (the owner)
          onDownload={section.listId || section.key === "done" ? describeListDownload(section) : undefined}
          onRename={section.listId ? (name) => saveLists(runLists.map((list) => list.id === section.listId ? { ...list, name } : list)) : undefined}
          onRemove={section.listId ? () => saveLists(runLists.filter((list) => list.id !== section.listId)) : undefined}
          renderRun={(run) => {
            const key = buildRunPath(run);
            return (
              <li key={`run:${key}`} className={`run-item${ticked.has(buildRunKey(run)) ? " ticked" : ""}`} draggable
                onDragStart={(event) => startRowDrag(event, buildRunKey(run))}>
                <input type="checkbox" checked={ticked.has(buildRunKey(run))} aria-label={`tick ${run.name}`}
                  onChange={() => toggle(buildRunKey(run))} />
                <button className={`row run-row${key === openKey ? " selected" : ""}`} onClick={() => onOpenRun(run.root, run.name)}
                  aria-current={key === openKey || undefined}
                  title={`${run.name}\n${run.root} · ${run.run_id || "no run record"} · ${run.status} · no video in the inbox`}>
                  <span className="row-main">
                    <span className="row-name">{run.display_name || run.name}</span>
                    {run.topic && !isSameName(run.topic, run.display_name || run.name) && (
                      <small className="topic truncate" title={run.topic}>{run.topic}</small>
                    )}
                    {(run.file_type || Boolean(run.size_bytes)) && (
                      <small className="muted">
                        {run.file_type && <span className="pill file-type">{run.file_type}</span>}{run.size_bytes ? formatSize(run.size_bytes) : ""}
                      </small>
                    )}
                  </span>
                  <span className={`pill link ${classifyRunPill(run)}`}
                    title={describeRunState(run) === "started" ? titleStoppedPhase(run.stopped_phase, phases) : undefined}>
                    {(findWaitingJobOfRun(run) && `${labelWaitingJob(findWaitingJobOfRun(run)!)} ›`)
                      || describeChangePill(lastJobsOfFolders.get(run.folder), run.has_document, run.status)?.label.concat(" ›")
                      || labelStatePill(describeRunState(run), run.stopped_phase, run.is_blocked, nameRunningSet(run))}
                  </span>
                </button>
              </li>
            );
          }}
          isOpen={Boolean(needle) || openSections.has(section.key)}
          onToggleOpen={() => toggleSection(section.key)}
          onSelectAll={(isSelecting, keys, others = []) => setTicked((previous) => {
            const next = new Set(previous);
            (keys ?? [...section.items.map((item) => item.video), ...section.runs.map(buildRunKey)])
              .forEach((key) => (isSelecting ? next.add(key) : next.delete(key)));
            if (isSelecting) others.forEach((key) => next.delete(key));
            return next;
          })}
          renderItem={(item) => {
            const lead = leadRun(item);
            const isOpened = item.runs.some((run) => buildRunPath(run) === openKey);
            return (
              <li key={item.video} draggable onDragStart={(event) => startRowDrag(event, item.video)}>
                <label className={`row selectable${ticked.has(item.video) ? " ticked" : ""}${isOpened ? " selected" : ""}`}
                  aria-current={isOpened || undefined}>
                  <input type="checkbox" checked={ticked.has(item.video)} disabled={Boolean(findPendingJob(item))}
                    aria-label={`tick ${item.display_name || item.name}`} onChange={() => toggle(item.video)} />
                  <span className="row-main" onClick={(event) => {
                    // a click on a video's name opens its page: its run's page, or a new video's own page
                    event.preventDefault();
                    if (lead) onOpenRun(lead.root, lead.name);
                    else onOpenVideo(item);
                  }}>
                    <span className="row-name" title={item.display_name ? `${item.display_name}\n${item.name}` : item.name}>
                      {item.display_name || item.name}
                    </span>
                    {item.topic && !isSameName(item.topic, item.display_name || item.name) && (
                      <small className="topic truncate" title={item.topic}>{item.topic}</small>
                    )}
                    <small className="muted" title={item.parts.map((part) => part.name).join("\n") || undefined}>
                      {item.file_type && <span className="pill file-type">{item.file_type}</span>}{formatSize(item.size_bytes)}{item.parts.length > 1 ? ` · ${item.parts.length} parts` : ""}
                    </small>
                  </span>
                  {(item.state === "new" || item.state === "started") && (
                    <button className="btn ghost icon-only small play-button"
                      aria-label={`${item.state === "new" ? "start a run of" : "resume the run of"} ${item.display_name || item.name}`}
                      title={item.state === "new" ? "start a run of this video, with the phases chosen below"
                        : "resume this video's run, with the phases chosen below"}
                      disabled={Boolean(findPendingJob(item))}
                      onClick={(event) => {
                        event.preventDefault();
                        void process([item]);
                      }}>
                      <Icon name="play" size={14} />
                    </button>
                  )}
                  {item.state === "new" && (
                    <button className="btn ghost icon-only small" title="rename this video (only before any run used it)"
                      aria-label={`rename ${item.name}`} onClick={(event) => {
                        event.preventDefault();
                        startRename(item);
                      }}>
                      <Icon name="pencil" size={14} />
                    </button>
                  )}
                  {lead && !findPendingJob(item) ? (
                    <button className={`pill link ${classifyItemPill(item)}`}
                      aria-label={`${describeChangePill(lastJobsOfVideos.get(item.video), lead.has_document, lead.status)?.label.concat(" ›")
                        ?? labelStatePill(item.state, lead.stopped_phase, lead.is_blocked, nameRunningSet(lead))} open the run of ${item.display_name || item.name}`}
                      aria-description={item.state === "started" ? titleStoppedPhase(lead.stopped_phase, phases) || undefined : undefined}
                      title={[item.state === "started" ? titleStoppedPhase(lead.stopped_phase, phases) : "",
                        `open its run: ${item.runs.map((run) => `${buildRunPath(run)} (${run.status})`).join(", ")}`]
                        .filter(Boolean).join("\n")}
                      onClick={(event) => {
                        event.preventDefault();
                        onOpenRun(lead.root, lead.name);
                      }}>
                      {describeChangePill(lastJobsOfVideos.get(item.video), lead.has_document, lead.status)?.label.concat(" ›")
                        ?? labelStatePill(item.state, lead.stopped_phase, lead.is_blocked, nameRunningSet(lead))}
                    </button>
                  ) : (
                    <NewItemPill job={findPendingJob(item)} name={item.display_name || item.name} onOpenJob={onOpenJob}
                      onOpenVideo={() => (lead ? onOpenRun(lead.root, lead.name) : onOpenVideo(item))}
                      hasDocument={Boolean(lead?.has_document)} />
                  )}
                </label>
              </li>
            );
          }} />
      ))}

      {/* The person's own list made here, named at once; rows are dragged onto its head */}
      <div className="new-list">
        {newListName === null ? (
          <button className="btn ghost small" aria-label="new list" title="make a list of your own and drag rows into it"
            onClick={() => setNewListName("")}>
            <Icon name="plus" size={14} /> New list
          </button>
        ) : (
          <input type="text" className="title-input" aria-label="name of the new list" placeholder="name of the new list"
            value={newListName} autoFocus onChange={(event) => setNewListName(event.target.value)}
            onBlur={() => createList(newListName)}
            onKeyDown={(event) => {
              if (event.key === "Enter") createList(newListName);
              if (event.key === "Escape") setNewListName(null);
            }} />
        )}
      </div>

      {foundArchive.length > 0 && (
        <div className="inbox-section archive">
          <div className="section-head">
            <span className="section-icon"><Icon name="archive" size={14} /></span>
            <button className="section-toggle" onClick={() => toggleSection("archive")} aria-expanded={isArchiveOpen}>
              <span>Archive</span>
              <span className="count">{foundArchive.length}</span>
              <span className={`caret${isArchiveOpen ? " open" : ""}`}><Icon name="chevron" size={14} /></span>
            </button>
          </div>
          {isArchiveOpen && (
            <ul className="rows">
              {foundArchive.map((entry) => (
                <li key={entry.name}>
                  <div className="row archived">
                    <span className="row-main">
                      <span className="truncate" title={entry.name}>{entry.name}</span>
                      {entry.topic && <small className="topic truncate" title={entry.topic}>{entry.topic}</small>}
                      <small className="muted">{entry.run_folder ? "run folder · " : entry.is_folder ? "meeting folder · " : ""}{formatSize(entry.size_bytes)}</small>
                    </span>
                    <button className="btn ghost small" title="move it back where it came from under the name it had"
                      onClick={() => void unarchive(entry.name)}>
                      <Icon name="restore" size={14} /> Unarchive
                    </button>
                    {entry.runs.length ? (
                      <button className={`pill link ${STATE_PILL_CLASSES[entry.state]}`}
                        title={`open its run: ${entry.runs.map((run) => `${buildRunPath(run)} (${run.status})`).join(", ")}`}
                        onClick={() => {
                          const run = leadRun(entry)!;
                          onOpenRun(run.root, run.name);
                        }}>
                        {formatRunState(entry.state)} ›
                      </button>
                    ) : (
                      <span className="pill">{entry.run_folder ? "run folder" : "no run"}</span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <InboxTrash refreshKey={trashMoves} onChanged={onChanged} onMessage={onMessage} />

      {renaming && (
        <Dialog title={`Rename ${renaming.name}`} onClose={() => setRenaming(null)}>
          <form className="rename-form" onSubmit={(event) => {
            event.preventDefault();
            void saveRename();
          }}>
            <label className="field">
              <span>New name (the extension stays when you leave it out)</span>
              <input type="text" value={nameDraft} autoFocus aria-label="new name"
                onChange={(event) => {
                  setNameDraft(event.target.value);
                  setRenameError("");
                }} />
            </label>
            {renameError && <p className="error">{renameError}</p>}
            <div className="dialog-actions">
              <button type="button" className="btn ghost" onClick={() => setRenaming(null)}>Cancel</button>
              <button type="submit" className="btn primary" disabled={!nameDraft.trim()}>Rename</button>
            </div>
          </form>
        </Dialog>
      )}

      {trashWarning && (
        <Dialog title="Move to trash?" onClose={() => setTrashWarning(null)}>
          <p>
            {trashWarning.unfinishedCount === 1
              ? "This file is not processed yet. Are you sure?"
              : "These files are not processed yet. Are you sure?"}
          </p>
          <ul className="warning-list">
            {trashWarning.runs.map((run) => (
              <li key={buildRunKey(run)}>
                <span className="truncate" title={run.name}>{run.name}</span>
                <span className={`pill ${STATE_PILL_CLASSES[describeRunState(run)]}`}>
                  {formatRunState(describeRunState(run))} · run folder
                </span>
              </li>
            ))}
            {trashWarning.items.map((item) => (
              <li key={item.video}>
                <span className="truncate" title={item.name}>{item.name}</span>
                <span className={`pill ${STATE_PILL_CLASSES[item.state]}`}>
                  {formatRunState(item.state)}
                </span>
              </li>
            ))}
          </ul>
          <p className="muted">The recordings move to inbox/.trash, where Restore brings them back. Their run folders stay
            where they are and stay listed in the inbox. A ticked run folder moves there whole.</p>
          <div className="dialog-actions">
            <button className="btn ghost" onClick={() => setTrashWarning(null)}>Keep them</button>
            <button className="btn danger-solid" onClick={() => {
              setTrashWarning(null);
              void moveTicked("trash", trashWarning.items, trashWarning.runs);
            }}>
              <Icon name="trash" /> Move to trash
            </button>
          </div>
        </Dialog>
      )}

      {pickingFor && (
        <ListPicker list={pickingFor} lists={runLists}
          rows={[...items.map((item) => ({ key: item.video, name: item.display_name || item.name, state: item.state,
              tags: mergeTags(item.tags ?? [], runs.filter((run) => item.runs.some((own) => buildRunPath(own) === buildRunPath(run)))
                .flatMap((run) => run.tags)) })),
            ...otherRuns.map((run) => ({ key: buildRunKey(run), name: run.display_name || run.name, state: describeRunState(run),
              tags: run.tags }))]}
          onAdd={(keys) => {
            moveRowsToList(keys, pickingFor.id);
            setPickingFor(null);
          }}
          onClose={() => setPickingFor(null)} />
      )}

      {isMergeOpen && (
        <MergeRecordingsDialog items={tickedItems} onClose={() => setIsMergeOpen(false)}
          onMerged={(name) => {
            setIsMergeOpen(false);
            setTicked(new Set());
            onMessage(`Merged ${tickedItems.length} recordings into ${name}`);
            onChanged();
          }} />
      )}
      {resetPhase !== null && (
        <Dialog title="Reset to phase" onClose={() => setResetPhase(null)}>
          <p className="muted">Removes every file and progress record of the phases after the one chosen below in each of these run folders,
            as if they never ran. 0 removes every phase. A running run is refused.</p>
          <div className="dialog-row">
            <label className="field">
              <span>Keep phases up to</span>
              <select value={resetPhase} aria-label="keep phases up to"
                onChange={(event) => setResetPhase(Number(event.target.value))}>
                <option value={0}>0 · none, every phase removed</option>
                {phases.filter((phase) => phase.number <= resetMax).map((phase) => (
                  <option key={phase.number} value={phase.number}>{phase.number} · {formatPhaseName(phase.name)}</option>
                ))}
              </select>
            </label>
          </div>
          <ul className="listing-names">
            {resetTargets.map((target) => <li key={buildRunPath(target)}>{target.name}</li>)}
          </ul>
          <div className="dialog-actions">
            <button className="btn ghost" onClick={() => setResetPhase(null)}>Cancel</button>
            <button className="btn danger-solid" disabled={isResetting} onClick={() => void resetRuns(resetTargets, resetPhase)}>
              {isResetting ? <><span className="spinner" aria-hidden="true" /> Resetting…</> : `Reset ${resetTargets.length}`}
            </button>
          </div>
        </Dialog>
      )}

      <div className="tab-foot">
        {/* what to do with the ticked rows, then which phases a run takes, then the run itself: one row each */}
        <div className="tab-foot-row" role="group" aria-label="ticked rows">
          <button className="btn ghost icon-only" disabled={!ticked.size}
            title="archive the ticked videos: they move to inbox/.archive and are listed under Archive with their runs; a ticked run folder moves there whole"
            aria-label="archive" onClick={() => void moveTicked("archive", tickedItems, tickedRuns)}>
            <Icon name="archive" />
          </button>
          <button className="btn ghost icon-only" disabled={Boolean(mergeRefusal)}
            title={mergeRefusal || "merge the ticked recordings into one recording in parts: one meeting recorded in several files"}
            aria-label="merge into one recording" onClick={() => setIsMergeOpen(true)}>
            <Icon name="merge" />
          </button>
          <button className="btn ghost icon-only" disabled={!resetTargets.length}
            title="reset the ticked videos' runs and the ticked run folders to a phase: the phases after it are removed"
            aria-label="reset to phase" onClick={() => setResetPhase(keptWholePhase())}>
            <Icon name="reset" />
          </button>
          <button className="btn ghost icon-only" disabled={!ticked.size} title="move the ticked videos and run folders to inbox/.trash"
            aria-label="move to trash" onClick={() => askToTrash(tickedItems, tickedRuns)}>
            <Icon name="trash" />
          </button>
        </div>
        <div className="tab-foot-row">
          {/* One action: Process starts the runs, and its ⚙ arrow holds how they run (phases, settings, models, tags)
              and Process every video; a choice other than the default is shown on the button itself */}
          <div className="split">
            <button className="btn primary" disabled={!processedRows.length}
              title={isAnyTicked ? "queue the ticked recordings and run folders with the run settings of the ⚙ arrow"
                : "queue every video with no run yet; tick videos to queue those instead"}
              onClick={() => void process(processedRows)}>
              <Icon name="play" /> {processLabel}
              {runSettings !== DEFAULT_RUN_SETTINGS && <small className="run-settings"> · {runSettings}</small>}
            </button>
            {/* The ⚙ opens a centred dialog showing every run choice at once, a card each: folded into a narrow menu
                they overloaded it (the owner) */}
            <button className="btn primary" aria-label="run settings" title="run settings"
              onClick={() => setIsRunSettingsOpen(true)}>
              <Icon name="settings" size={16} />
            </button>
            {isRunSettingsOpen && (
              <Dialog title={`Run settings · ${describeRunTarget(tickedRows, newItems.length)}`}
                onClose={() => setIsRunSettingsOpen(false)}>
                <PhaseSelector phases={mediumPhases ?? phases} phaseModels={phaseModels}
                  selection={selection} onChange={setSelection} layout="tabs"
                  workflows={workflows}
                  // the video workflow applies to every video processed, and only to them (the owner: "select the fast
                  // profile for all selected video and start them"); the PDF choices to every ticked PDF
                  extraTabs={[...(processedRows.some(isVideoItem) ? [{ key: "video", label: "Video",
                    summary: `speed ${describeSpeed(selection, runDefaults)}`,
                    body: <div className="field">
                      <span>Workflow speed</span>
                      <WorkflowSpeedChoice selection={selection} onChange={setSelection} defaults={runDefaults} />
                      <small className="muted">A preset sets every setting that changes how long a video run takes;
                        a setting changed by hand makes it Custom. PDFs, audio and office documents keep their own
                        way.</small>
                    </div> }] : []),
                    ...(tickedRows.some(isPdfItem) ? [{ key: "pdf", label: "PDF", summary: describePdfChoices(selection),
                    body: <>
                      <PdfChoices selection={selection} onChange={setSelection} defaults={runDefaults} isDropdown />
                      {changeableRows.length > 0 && (
                        <section className="change-documents" aria-label="change the ticked documents">
                          <h4>Change the {changeableRows.length} ticked document{changeableRows.length === 1 ? "" : "s"}</h4>
                          <p className="muted">Runs only the change on each finished document, in its own run folder;
                            nothing is processed again from the start.</p>
                          <DocumentPassPanel profile={selection.profile} lastPass={changedPass} isDropdown
                            onQueue={(pass, wishes, source) => void changeDocuments(changeableRows, pass, wishes, source)} />
                        </section>
                      )}
                    </> }] : [])]} />
                <div className="dialog-actions run-settings-actions">
                  <button className="btn ghost" onClick={() => { setIsRunSettingsOpen(false); void process(items); }}>
                    <Icon name="play" /> Process every video <small className="muted">done ones too</small>
                  </button>
                  <span className="spacer" />
                  <button className="btn" onClick={() => setIsRunSettingsOpen(false)}>Done</button>
                  <button className="btn primary" disabled={!processedRows.length}
                    onClick={() => { setIsRunSettingsOpen(false); void process(processedRows); }}>
                    <Icon name="play" /> {processLabel}
                  </button>
                </div>
              </Dialog>
            )}
            {versionAsk && (
              <VersionAskDialog runs={versionAsk.several} onClose={() => setVersionAsk(null)}
                displayNames={Object.fromEntries(runs.map((run) => [`${run.root}/${run.name}`, run.display_name]))}
                onChoose={(version) => void downloadVersion(versionAsk.refs, versionAsk.title, version)} />
            )}
            {redoAsked && (() => {
              const done = redoAsked.filter((item) => item.state === "done");
              const others = redoAsked.filter((item) => item.state !== "done");
              return (
                <Dialog title="Process done recordings again?" onClose={() => setRedoAsked(null)}>
                  <p>{done.length === 1 ? "1 of the recordings is" : `${done.length} of the recordings are`} already
                    done. Processed again, {done.length === 1 ? "it runs" : "they run"} every phase from the start in
                    {done.length === 1 ? " its" : " their"} own run folder, and the finished document is written over.</p>
                  <ul className="redo-list">{done.map((item) => <li key={item.video}>{item.name}</li>)}</ul>
                  <div className="dialog-actions">
                    <button className="btn" onClick={() => setRedoAsked(null)}>Cancel</button>
                    <span className="spacer" />
                    {others.length > 0 && (
                      <button className="btn" onClick={() => { setRedoAsked(null); void process(others, true); }}>
                        Skip the done ones
                      </button>
                    )}
                    <button className="btn primary" onClick={() => { setRedoAsked(null); void process(redoAsked, true); }}>
                      Process again
                    </button>
                  </div>
                </Dialog>
              );
            })()}
          </div>
        </div>
      </div>
    </div>
  );
}
