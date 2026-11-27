// The calls the page makes to `./i2k serve` (src/services/web_api_service.py). Field names are the server's.

export interface InboxItem {
  name: string;
  video: string;
  path: string;
  size_bytes: number;
  run_folder: string;
  output: string;
  // the file has no picture (an audio file, or a folder of audio parts)
  is_audio: boolean;
  // the file's own type, "video", "audio" or "pdf": a video processed as audio is still a video
  file_type?: string;
  // a PDF's pages, which pick its default processing and shaping; 0 for anything else
  page_count?: number;
  // a video's or an audio recording's length in seconds, which bounds the run form's Range; 0 for a PDF or unknown
  duration_s?: number;
  is_finished: boolean;
  // each run's `stopped_phase`: the first phase of its flow a started run has not completed; 0 for a run going or done;
  // `is_blocked` when a phase is recorded blocked or failed
  runs: { root: string; name: string; status: string; is_live: boolean; has_document: boolean; topic: string; display_name: string;
    stopped_phase: number; is_blocked?: boolean; worker_set?: string }[];
  // a meeting recorded in several files: its parts in the order they are read; empty for a single video
  parts: { name: string; size_bytes: number; start_s?: number }[];
  topic: string;
  // the name its run is shown under in place of the file name (its AI name, from the Manager's Rename); empty for none
  display_name: string;
  // the tags given on its page before its first run
  tags?: string[];
  // its run folders that hold no phase's output (reset to 0): the video's still, so listed nowhere else
  empty_runs?: { root: string; name: string }[];
  state: "new" | "started" | "running" | "done";
  // the moment its state is dated by, in seconds: came into the inbox (new), its run began (running), last ran
  // (started), its document was written (done)
  state_at: number;
}

export interface RunSummary {
  root: string;
  name: string;
  folder: string;
  // the inbox path of the recording the run keeps in its folder, by which a list holds it; "" when it keeps none
  origin_video?: string;
  // the project the run is in; "default" for every run no project holds
  project?: string;
  path: string;
  run_id: string;
  status: string;
  started_at: string;
  video: string;
  is_live: boolean;
  has_document: boolean;
  topic: string;
  // the tags the owner gave the run, from this page or `i2k tags`
  tags: string[];
  // the name the run is shown under in place of its folder's name (its AI name, from the Manager's Rename); empty for none
  display_name: string;
  // an audio recording: no frames, its speech structured by phase 2
  is_audio: boolean;
  // the recording's own file, "video", "audio" or "pdf"; "" when the run names none
  file_type?: string;
  // the bytes of the recording kept in the run folder once the run is done; 0 when it keeps none
  size_bytes?: number;
  // a PDF's processing mode, "pages-as-images" or "text-only"; "" or absent for any other recording
  pdf_mode?: string;
  // the LLM pass phase 7 was asked to give the document, "cleanup", "rewrite" or "knowledge"; "" or absent for none
  document_pass?: string;
  // the ingesting profile its document is written in (`knowledge` unless a tag, phase 2 or a conversion chose another)
  ingesting_profile: string;
  // the part of the input the run processes ("12:30 to 1:05:00", "pages 12 to 40"); "" or absent for the whole input
  input_range?: string;
  // the settings profile the run read (--profile), empty for the default settings alone
  settings_profile?: string;
  // whether a tag, phase 2 or a conversion chose it yet; an audio run's is picked in phase 2
  is_profile_chosen: boolean;
  updated_at: number;
  // the moment its state is dated by, in seconds, as an inbox video's `state_at`
  state_at: number;
  // the first phase of its flow it has not completed, when it is started (not going, no document); 0 otherwise
  stopped_phase: number;
  // the phase a live run is in; 0 or absent for a run not going
  live_phase?: number;
  // the worker set the run chose; "" or absent for the default set
  worker_set?: string;
  // a phase recorded blocked or failed: a resume asks the blocked step again
  is_blocked?: boolean;
  // made by Branch or Convert from another run: a run of its own, never folded into its video's row
  is_derived?: boolean;
}

export interface RecordingPart {
  name: string;
  size_bytes: number;
  start_s: number;
  duration_s: number;
}

// One batch of phase 3 as a place in the recording: its title, its first and last second of the meeting, and its summary.
export interface BatchMark {
  title: string;
  start_s: number;
  end_s: number;
  summary: string;
}

export interface PhaseRow {
  number: number;
  name: string;
  status: string;
  // why a complete phase wrote nothing, as an audio run's phase 3 whose profile prepares nothing: "nothing to prepare"
  note?: string;
  // the card's title and help for this run's medium: an audio recording's phase 2 is "speech preparing"
  title?: string;
  summary?: string;
  reads?: string;
  writes?: string;
  is_model_used?: boolean;
  // a worker of this phase runs or ran, so its card opens what it thought and did
  has_worker?: boolean;
}

// one follow-up of phase 4 or 5 and how often it blocked, was accepted and was reset (`i2k blocks` prints the same)
export interface StepOutcome {
  phase: number;
  step: string;
  blocked: number;
  accepted: number;
  reset: number;
  blocks: { reason: string; summary: string; at: string }[];
}

export interface RunDetail extends RunSummary {
  phases: PhaseRow[];
  frames: Record<string, number>;
  // phase 4's follow-ups in its three stages (table of contents, chapters, final summary), each counted by status:
  // accepted, blocked, waiting; the total is known once the agenda named the chapters
  document_stages: { name: string; counts: Record<string, number> }[];
  // the published document's path inside the run folder, for Open and Download; empty before it is published
  document_file?: string;
  // the document as phase 4 wrote it and after each clean up or rewrite, oldest first, the last the published one;
  // empty while no clean up or rewrite ran
  document_versions?: DocumentVersion[];
  // the profiles a finished audio run can write its document in again in its own folder, from the same notes
  appendable_profiles?: string[];
  // the document's chapters, one per agenda point; empty before the agenda is written
  chapters?: { number: number; title: string }[];
  is_document_total_known: boolean;
  blocked_items: string[];
  step_outcomes: StepOutcome[];
  // the problems the workers reported through `report_problem`; `file` is empty when none was reported; `open` counts
  // those no line of worker-feedback-answers.md answers at their count, `newest_answer` is "" while the newest is open
  worker_feedback: {
    entries: number; reports: number; open: number; newest_kind: string; newest_summary: string;
    newest_answer: string; file: string;
  };
  // phase 3's seconds so far over the frames it finished; 0 while none is finished
  seconds_per_frame: number;
  // an audio recording's phase 3, when its ingesting profile prepares the notes in steps: each step's calls done of
  // how many, `total` null while the step it goes through has not listed its items
  preparation?: { title: string; done: number; total: number | null }[];
  // the video workflow the run takes from phase 3 on; "" for an audio recording, which takes the audio workflow
  workflow?: string;
  // the Workflow speed chosen for the run; none when the settings decided
  speed?: RunSpeed | null;
  // the worker set the run keeps ("" when it kept none: it runs on the settings' default)
  worker_set?: string;
  // the run and phase this run was branched from, null for a run that is no branch
  branch?: { from: string; phase: number; at: string } | null;
  // phase 2's stages in order, each with how far it is: the rules count seconds of video, the classifier and the model frames
  frames_preparing?: StageProgress[];
  // phase 1's stages, the same shape: the transcription, then the speakers
  transcribing?: StageProgress[];
  // phase 7's parts, in order: the clean up, rewrite or knowledge pass works one part at a time
  document_pass_parts?: PassPart[];
  // what the run said about its own work: the anti-pattern rules its batches reported, grouped and counted the way
  // phase 6 groups them, so the card and 06_critique-collection/critique-collection.md never disagree
  critique: {
    files: number; rules: number; anti_patterns: number;
    top: string; top_batches: number; top_frames: number;
    // the rule set phase 6 wrote, when it wrote one: the path to open in the reading mode
    rule_set: string;
  };
  timing: { phases: Record<string, number>; total_s: number; is_counting: boolean };
  has_recording: boolean;
  // a done run keeps its recording in its folder (`recording/`); phases 1 and 2 wait until it is back in the inbox
  is_recording_kept: boolean;
  // a done run whose recording is still in the inbox: it can be kept in the run folder
  can_keep_recording: boolean;
  // a running run whose settings files changed after it read them: the change waits for a stop and a resume
  has_newer_settings?: boolean;
  // Compress recording: the kept recording re-encoded to 720p in place; absent or {} when never compressed
  recording_compression?: RecordingCompression | Record<string, never>;
  recording_parts: RecordingPart[];
  batches: BatchMark[];
  live_pids: number[];
  log: string;
  signal: RunSignal | null;
  // the run holds at its pause now (an older server never says so)
  is_held?: boolean;
  // what the live run does right now, in words, and since when (an older server never says)
  activity?: { text: string; since: string } | null;
}

// One stage of phase 1 or 2 and how far it is
// One part of phase 7's pass: "stopped" is a part that started while no run goes on
export interface PassPart {
  number: number;
  status: "waiting" | "running" | "done" | "stopped";
  blocks: number;
  // its size, read from the document the pass started from
  tokens: number;
  sections: number;
  // the figures among its blocks
  figures?: number;
  // the document as this part left it, or as it stands while the part runs, its path in the run folder
  document?: string;
  // a running part's model call under way: its thinking so far (a path; empty when none), words and seconds
  thinking?: string;
  thinking_words?: number;
  thinking_seconds?: number;
  // a running part waiting for a model host with no working model: what is wrong (empty when not waiting), and how long
  host_wait?: string;
  host_wait_seconds?: number;
  calls: number;
  // how often its conversation reached the size limit and was compacted into the worker's summary
  compactions?: number;
  // how often its worker dropped results it no longer needed from its conversation
  prunes?: number;
  // its calls' average writing speed, output tokens a second; null before a call of it was measured
  tokens_per_second?: number | null;
  seconds: number;
}

// A worker's live panel, in every workflow (`GET <run>/document-pass/parts/<n>`, `<run>/phases/<n>/worker`,
// `<run>/document-chat`): each step it took, what the program answered, and the thinking of the call under way
export interface WorkerToolStep { at: number | string | null; call: number; tool: string; summary?: string; args: string; result: string }

// one model call: its thinking, its words, and the tools it called with what the program answered
export interface WorkerCallTurn { at: number | string | null; call: number; thinking: string; text: string; steps: WorkerToolStep[] }

export interface WorkerActivity {
  number?: number;
  // a phase's worker: the step its newest thinking belongs to, and whether it holds a conversation it can answer in
  step?: string;
  can_ask?: boolean;
  // false for a worker that takes no message while it works
  can_send?: boolean;
  is_worker_running?: boolean;
  // `summary`: the step's arguments in one short line, the edits' actions and blocks or the section read
  steps: WorkerToolStep[];
  // a part's model calls, each with its own thinking (the owner: "make each turn presist own thinking")
  turns?: WorkerCallTurn[];
  thinking: string;
  thinking_at: number | null;
  // the last call's writing speed: the host's token count once it ended, else estimated from the text written
  speed?: { tokens_per_second: number | null; is_estimated: boolean; prompt_tokens: number; is_writing: boolean };
  // what the worker does now ("reading the prompt", "thinking", "writing its answer", "the program runs its tools")
  doing?: { what: string; since: number | null };
  // what the person wrote to the worker from this panel
  messages?: PassPartMessage[];
}

// One message to a part's worker: `is_sent` once the worker got it, `answer` the words it wrote first after it
export interface PassPartMessage {
  at: number | null;
  text: string;
  role: "user" | "system";
  is_question: boolean;
  is_sent: boolean;
  // when the worker took it, in seconds; none for one taken before the time was kept
  sent_at?: number | null;
  answer: string;
}

export interface StageProgress {
  name: string;
  unit: string;
  // "skipped": a PDF's step the reading never needed, such as its scanned pages when it has none
  status: "waiting" | "running" | "done" | "failed" | "skipped";
  done: number;
  total: number | null;
  frames: number | null;
  kept?: number | null;
  seconds: number;
  // a running stage that counts: the time left at its pace so far, and the seconds since it last counted one more
  seconds_left?: number | null;
  seconds_since_progress?: number | null;
  // a filter stage's removed frames, counted from the dropped frames themselves
  removed?: { slug: string; path: string; covered_by: string[]; reason: string }[];
}

// What a live run is asked to do at its next check (the run's control file): hold, finish its item and stop, stop, or
// go on as before
export type RunSignal = "pause" | "drain" | "stop" | "continue";

export interface JobRequest {
  video: string;
  output_root: string;
  only_phase: number | null;
  from_phase: number | null;
  to_phase: number | null;
  worker_indexes: number[];
  is_resuming: boolean;
  is_dry_run: boolean;
  // the choices the job was queued with, as the server's job record keeps them; a field an older record lacks is left out
  profile?: string;
  tags?: string[];
  ingesting_profile?: string;
  is_run_as_audio?: boolean;
  pdf_mode?: "" | PdfMode;
  pdf_ocr_engine?: "" | PdfOcrEngine;
  document_pass?: "" | DocumentPass;
  document_wishes?: string;
  document_source?: "" | DocumentSource;
  knowledge_filter_model?: "" | KnowledgeFilterModel;
  range_start?: string;
  range_end?: string;
  range_pages?: string;
  workflow?: string;
  // the worker set the job is on ("" leaves the run folder's, else the settings' default)
  worker_set?: string;
  // the Workflow speed preset, and `KEY=VALUE` pairs of the settings changed from it
  speed_preset?: string;
  speed_settings?: string[];
  // `PHASE=NAME` pairs, as the run line's `--phase-model` takes them
  phase_models?: string[];
  // what the job is for when the run line alone does not say it, such as a conversion to another profile
  purpose?: string;
  // what the queue does after this job's last failed try; "" takes the queue.onFailure setting
  on_failure?: "" | JobOnFailure;
}

// After a job's last failed try: start the next job, or hold the queue until the person retries or skips it
export type JobOnFailure = "continue" | "block";

// A job's state in the queue: waiting, running, waiting for its retry, holding the queue, or how it ended
export type JobStatus = "queued" | "running" | "retrying" | "blocked" | "finished" | "incomplete" | "refused" | "stopped"
  | "cancelled" | "lost" | "crashed";

export interface Job {
  job_id: string;
  request: JobRequest;
  status: JobStatus;
  pid: number | null;
  exit_code: number | null;
  queued_at: string;
  started_at: string;
  ended_at: string;
  command: string[];
  // a running job whose run folder is gone after its run made it
  is_output_missing: boolean;
  // how many times its process started (1 on the first try), and how many tries it gets in all; a record older than the
  // retries lacks both
  attempt?: number;
  tries?: number;
  // when a job waiting for its retry starts again
  retry_at?: string;
  // the worker set a job that may still run goes on (its request's, its run folder's or its profile's); "" once ended
  lane?: string;
}

export interface PhaseSelection {
  only_phase: number | null;
  from_phase: number | null;
  to_phase: number | null;
  worker_indexes: number[];
  is_resuming: boolean;
  is_dry_run: boolean;
  // a settings profile (`playbook.<name>.json`) for this run; "" runs on the default settings
  profile: string;
  // tags the run folder gets before phase 1; one naming an ingesting profile picks it
  tags: string[];
  // the ingesting profile chosen by hand, over any tag and phase 2's own pick; "" leaves it to them
  ingesting_profile: string;
  // a video processed as an audio recording: its sound alone, no frame taken
  is_run_as_audio: boolean;
  // the video workflow from phase 3 on, "quality" or "fast-processing"; "" leaves the run folder's or the settings'
  workflow?: string;
  // the worker set the run is on, whose models and hosts it calls: "" leaves the run folder's, else the settings' default
  worker_set?: string;
  // the Workflow speed preset ("" leaves the run folder's or the settings'), and the settings changed from it by hand
  speed_preset?: string;
  speed_settings?: Record<string, SpeedValue>;
  // how a PDF is read: "pages-as-images" (each page a picture, read in batches) or "text-only" (its text alone); ""
  // leaves the run folder's or the settings' choice
  pdf_mode?: "" | PdfMode;
  // what reads a PDF's scanned pages in phase 1: "tesseract" or "unlimited-ocr"; "" leaves the settings' pdf.ocrEngine
  pdf_ocr_engine?: "" | PdfOcrEngine;
  // phase 7: a model's clean up or rewrite of the published document, and the person's own wishes for it
  document_pass?: "" | DocumentPass;
  document_wishes?: string;
  // what that pass starts from; "" is the original
  document_source?: "" | DocumentSource;
  // which model drops a video's frames with no knowledge in phase 2; "" leaves the settings' frames.knowledgeFilterModel
  knowledge_filter_model?: "" | KnowledgeFilterModel;
  // the part of the input the run processes: a start and an end time (seconds, m:ss or h:mm:ss) of a recording, or a
  // PDF's "first-last" pages; "" is the whole input
  range_start?: string;
  range_end?: string;
  range_pages?: string;
  // phase -> the configured model it calls in this run; a phase left out uses the settings' choice
  phase_models?: Record<string, string>;
}

// What `GET /api/phase-models` answers: the configured models, and each phase that calls one with its default
// A PDF's processing modes, named and explained once for the run form and the run page's Copy and run again menu (the
// owner's names: by what the model reads)
export type PdfMode = "pages-as-images" | "text-only" | "markdown-conversion";
export const PDF_MODES: { value: PdfMode; label: string; hint: string }[] = [
  { value: "text-only", label: "text only", hint: "the model reads the PDF's text alone, with no picture and no batch" },
  { value: "pages-as-images", label: "pages as images", hint: "the model sees each page as a picture, read in batches" },
  { value: "markdown-conversion", label: "markdown conversion", hint: "the PDF is converted to markdown with no model at all, each figure where it stood" },
];

// Phase 7, after the document is published: a model cleans it up, rewrites it, or writes its facts into one section per
// subject; `description` is the short line the Change this document menu shows beside the name,
// `noun` how a sentence names the pass, `instruction` the file and step the Instructions editor opens for it
export type DocumentPass = "cleanup" | "rewrite" | "knowledge";
// What a pass starts from: the document before any change, or the document the last pass left
export type DocumentSource = "original" | "current";
export interface InstructionTarget {
  id: string;
  workflow: string;
  step: string;
}
const CLEANUP_INSTRUCTION: InstructionTarget = { id: "instructions/_shared/document-cleanup.md", workflow: "pdf",
  step: "document_cleanup" };
export const DOCUMENT_PASSES: { value: DocumentPass; label: string; description: string; noun: string; hint: string;
  instruction: InstructionTarget }[] = [
  { value: "cleanup", label: "clean up with LLM", description: "removes noise, keeps every sentence", noun: "clean up",
    hint: "a model takes out what the export left that a reader does not need", instruction: CLEANUP_INSTRUCTION },
  { value: "rewrite", label: "rewrite with LLM", description: "better structure and writing", noun: "rewrite",
    hint: "a model cleans up and improves the document's structure and writing", instruction: CLEANUP_INSTRUCTION },
  { value: "knowledge", label: "knowledge reshaping with LLM",
    description: "reshapes the document into a knowledge-focused document, in parts, never the whole document at once",
    noun: "knowledge reshaping",
    hint: "a model writes the business facts into one section per subject, without ticket numbers, sprints or authors",
    instruction: { id: "instructions/_shared/document-knowledge.md", workflow: "pdf", step: "document_knowledge" } },
];

export function describeDocumentPass(pass: DocumentPass): string {
  return DOCUMENT_PASSES.find((entry) => entry.value === pass)?.noun ?? pass;
}

// A panel asks the page to open one instruction file in the Instructions editor; the page listens for it
export const OPEN_INSTRUCTION_EVENT = "i2k-open-instruction";
export function openInstruction(target: InstructionTarget): void {
  window.dispatchEvent(new CustomEvent<InstructionTarget>(OPEN_INSTRUCTION_EVENT, { detail: target }));
}

// The flow a PDF takes under `mode`, as `/api/phases` names it
export function describePdfFlow(mode: string | undefined): string {
  return mode === "pages-as-images" ? "pdf-pages" : mode === "markdown-conversion" ? "pdf-markdown-conversion" : "pdf-text-only";
}

// The run form's two PDF choices ("processing flow and final document
// shaping, the second one's options depend on the first"): how the PDF is processed, and how the document is shaped.
// They map onto the run's stored mode, pass and ingesting profile, so runs made before read the same.
export type PdfProcessing = "text" | "pages";
export const PDF_PROCESSINGS: { value: PdfProcessing; label: string; hint: string }[] = [
  { value: "text", label: "Text", hint: "the PDF's text is read, with its figures cut out" },
  { value: "pages", label: "Pages as images", hint: "a model looks at each page as a picture, a few pages at a time" },
];
// The shapings no model writes: the document as the PDF stands, alone or then cleaned up or rewritten by a model.
// Offered for Text alone; every shaping a model writes is an ingesting profile
export const PDF_STANDING_SHAPINGS: { value: string; label: string; hint: string; pass: "" | DocumentPass }[] = [
  { value: "markdown", label: "Markdown", pass: "",
    hint: "no model: the PDF's text and figures written out as markdown, in seconds to minutes" },
  { value: "cleanup", label: "Markdown + LLM clean up", pass: "cleanup",
    hint: "then a model takes out what a reader does not need, such as a wiki's page tree or likes" },
  { value: "rewrite", label: "Markdown + LLM rewrite", pass: "rewrite",
    hint: "then a model also improves the structure and the writing, keeping every fact" },
  // its own value: "knowledge" is the profile a model writes from the pages, in phase 4
  { value: "llm-knowledge", label: "Markdown + LLM knowledge reshaping", pass: "knowledge",
    hint: "then a model reshapes it into a knowledge-focused document, in parts, never the whole document at once" },
];
// Every other profile shapes people speaking (a meeting's decisions, a conversation's turns, a lesson, a sprint): a
// PDF has no speakers, so it is offered knowledge alone (the owner: "some options are for audio or video flows")
const PDF_PROFILES = ["knowledge"];

export function listPdfProfiles<T extends { name: string }>(profiles: T[]): T[] {
  return profiles.filter((profile) => PDF_PROFILES.includes(profile.name));
}

// A PDF's Document choice as the page names it: a markdown choice by its label, the knowledge profile as what it gives,
// a document a model writes (the owner: "knowledge" beside "Markdown + LLM knowledge reshaping" read as one thing)
const PDF_PROFILE_LABELS: Record<string, string> = { knowledge: "LLM-written document" };
export function describePdfShaping(value: string): string {
  return PDF_STANDING_SHAPINGS.find((entry) => entry.value === value)?.label ?? PDF_PROFILE_LABELS[value] ?? value;
}

// The mode a PDF of `pageCount` pages takes when its run chooses none: the settings' mode, or under "by-size" the one
// its page count picks, as the server picks it; "" while that cannot be told (no page count)
export function choosePdfMode(defaults: RunDefaults | null, pageCount?: number): "" | PdfMode {
  if (!defaults) return "";
  if (defaults.pdf_mode !== "by-size") return defaults.pdf_mode;
  if (!pageCount) return "";
  if (pageCount <= (defaults.pdf_images_page_limit ?? 0)) return "pages-as-images";
  return pageCount > (defaults.pdf_model_writing_page_limit ?? Infinity) ? "markdown-conversion" : "text-only";
}

export function readPdfProcessing(mode: string | undefined): "" | PdfProcessing {
  if (!mode) return "";
  return mode === "pages-as-images" ? "pages" : "text";
}

// The shaping a mode, a pass and a profile stand for; "" while the run keeps the default
export function readPdfShaping(selection: { pdf_mode?: string; document_pass?: string; ingesting_profile?: string }): string {
  if (selection.pdf_mode === "markdown-conversion") {
    return PDF_STANDING_SHAPINGS.find((entry) => entry.pass === (selection.document_pass ?? ""))?.value ?? "markdown";
  }
  return selection.ingesting_profile ?? "";
}

// The default shaping of a processing under the mode the run would take: Markdown where that mode is the markdown
// conversion, else the default profile
export function chooseDefaultShaping(processing: PdfProcessing, defaultMode: "" | PdfMode): string {
  return processing === "text" && defaultMode === "markdown-conversion" ? "markdown" : "knowledge";
}

// The run's mode, pass and profile for a processing and a shaping ("" for each kept default); both kept default send
// none, so the server picks by the PDF's size
export function buildPdfChoice(processing: "" | PdfProcessing, shaping: string, defaultMode: "" | PdfMode):
    { pdf_mode: "" | PdfMode; document_pass: "" | DocumentPass; ingesting_profile: string } {
  if (!processing && !shaping) return { pdf_mode: "", document_pass: "", ingesting_profile: "" };
  const standing = PDF_STANDING_SHAPINGS.find((entry) => entry.value === shaping);
  const resolved = processing || (standing ? "text" : readPdfProcessing(defaultMode) || "text");
  const chosenShaping = shaping || chooseDefaultShaping(resolved, defaultMode);
  const chosenStanding = PDF_STANDING_SHAPINGS.find((entry) => entry.value === chosenShaping);
  if (resolved === "text" && chosenStanding) {
    return { pdf_mode: "markdown-conversion", document_pass: chosenStanding.pass, ingesting_profile: "" };
  }
  // a profile kept default is sent as none, the run's own default; a standing shaping kept from Text has no meaning for
  // page pictures, and the default profile takes its place
  const profile = chosenStanding || !shaping ? "" : chosenShaping;
  return { pdf_mode: resolved === "pages" ? "pages-as-images" : "text-only", document_pass: "", ingesting_profile: profile };
}

// A mode as the page names it; one an older run recorded under another word is shown as written
// A PDF run's mode in the run form's two words: its Processing and its Document (the owner: processing flow, then
// document shaping)
// How a PDF run is processed and how its document is made, named as the run form's lists name them: the markdown
// conversion with the LLM pass phase 7 was asked for ("Markdown + LLM knowledge reshaping"), a model-written document
// with the pass after it ("LLM-written document, then clean up with LLM"), so the pill says what the document is
export function describePdfChoice(mode: string, pass = ""): { processing: string; document: string } {
  const processing = mode === "pages-as-images" ? "Pages as images" : "Text";
  if (mode === "markdown-conversion") {
    const shaping = PDF_STANDING_SHAPINGS.find((entry) => entry.pass === pass) ?? PDF_STANDING_SHAPINGS[0];
    return { processing, document: shaping.label };
  }
  const passEntry = DOCUMENT_PASSES.find((entry) => entry.value === pass);
  const written = describePdfShaping("knowledge");
  return { processing, document: passEntry ? `${written}, then ${passEntry.label}` : written };
}

export function describePdfMode(mode: string): { label: string; hint: string } {
  return PDF_MODES.find((entry) => entry.value === mode) ?? { label: mode.replace(/-/g, " "), hint: "" };
}

// What reads a PDF's scanned pages in phase 1 (pdf.ocrEngine)
export type PdfOcrEngine = "tesseract" | "unlimited-ocr";

// The run form's Scans menu: what reads a scanned page (a picture of a page); a page with text is read as it is
export const PDF_SCAN_ENGINES: { value: PdfOcrEngine; label: string; hint: string }[] = [
  { value: "unlimited-ocr", label: "Unlimited-OCR-2",
    hint: "a scanned page (a picture of a page) is read by Unlimited-OCR-2, which keeps nearly every word, about 8 to 20 s a page" },
  { value: "tesseract", label: "Tesseract",
    hint: "a scanned page is read by Tesseract: about 1.7 s a page, but a noisy scan can lose most of its words" },
];

// Which model drops a video's frames with no knowledge in phase 2 (frames.knowledgeFilterModel), named by its publisher
// and full name
export type KnowledgeFilterModel = "google-SigLIP-2" | "liquidai-d1-2B";
export const KNOWLEDGE_FILTER_MODELS: { value: KnowledgeFilterModel; label: string; hint: string }[] = [
  { value: "google-SigLIP-2", label: "google-SigLIP-2",
    hint: "the frame classifier trained here on Google's SigLIP 2: drops frames of people, blank or unreadable screens" },
  { value: "liquidai-d1-2B", label: "liquidai-d1-2B",
    hint: "Liquid AI's decision model, no training: asked whether a frame holds knowledge, and which kind of empty frame it is (people, desktop, blank, joining), about 0.7 s a frame" },
];

// What a run started now takes for each choice the run form leaves untouched (`GET /api/run-defaults`)
export interface RunDefaults {
  // a mode for every PDF, or "by-size": the PDF's page count picks it, by the two limits below
  pdf_mode: PdfMode | "by-size";
  pdf_images_page_limit?: number;
  pdf_model_writing_page_limit?: number;
  pdf_ocr_engine: PdfOcrEngine;
  knowledge_filter_model: KnowledgeFilterModel;
  workflow: string;
  // the Workflow speed menu; an older server sends none
  speed?: SpeedOptions;
  // the extra wishes each LLM pass was last given (settings pdf.cleanupWishes and pdf.rewriteWishes); an older server
  // sends none
  document_wishes?: Partial<Record<DocumentPass, string>>;
}

// The worker sets a run can be on (`GET /api/worker-sets`): `default` first, then each set the settings name, with the
// settings it changes; `default_set` is the one a run with no choice is on
export interface WorkerSets {
  default_set: string;
  // machine: where the set runs, in the owner's words ("main machine"); empty when the settings name none
  sets: { name: string; changes: string[]; machine?: string }[];
}

// A Workflow speed setting's value: a switch, a count or a choice
export type SpeedValue = boolean | number | string;
// One setting of the Workflow speed menu, by its playbook name, with what it does
export interface SpeedSetting {
  key: string;
  label: string;
  hint: string;
  kind: "boolean" | "number" | "choice";
  min?: number;
  max?: number;
  options: { value: string; label: string }[];
}
// The Workflow speed menu (`run-defaults`' `speed`): its settings, each preset's values, the values the settings hold
// now, and the preset they are ("custom" when none)
export interface SpeedOptions {
  settings: SpeedSetting[];
  presets: Record<string, Record<string, SpeedValue>>;
  current: Record<string, SpeedValue>;
  default_preset: string;
}
// A run's Workflow speed, as its folder keeps it
export interface RunSpeed {
  preset: string;
  values: Record<string, SpeedValue>;
  name: string;
}

export interface PhaseModels {
  models: { name: string; spec: string }[];
  phases: { phase: string; number: number; title: string; default: string }[];
}

// What the tag box suggests (`GET /api/tags`): every tag runs have, and each ingesting profile with the tags that pick it
export interface IngestingProfile {
  name: string;
  aliases: string[];
  // what kind of recording it is for
  description: string;
}

export interface KnownTags {
  tags: string[];
  profiles: IngestingProfile[];
}

export interface CommandOutput {
  exit_code: number;
  output: string;
}

// A frame Refine judged: the frame knowledge classifier's drop score and decision, for a frame phase 3 has not read
export interface FrameProposal {
  slug: string;
  path: string;
  timestamp: number;
  dropScore: number;
  decision: "keep" | "drop";
}

export interface UploadOutcome {
  name: string;
  saved_as: string;
  error: string;
  // why it was not saved: `processed` (a done run has it), `in_inbox`, `in_upload` (twice in one drop) or `refused`;
  // `same_as` names the run or file it matched (by SHA-256), `duplicate_of` the done run to open
  kind?: "saved" | "processed" | "in_inbox" | "in_upload" | "refused";
  same_as?: string;
  duplicate_of?: { root: string; name: string } | null;
}

// Sent on the window when the server answers 401: the session ended, so the page shows its sign-in again
export const SIGNED_OUT_EVENT = "i2k-signed-out";

// A refusal in one line, "<status>: <why>": the server's `detail`, which a request it could not validate gives as a
// list of problems, each with its own words; with no `detail`, the fallback (the status text, or the body as text)
function describeRefusal(status: number, body: unknown, fallback: string): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return `${status}: ${detail}`;
  if (Array.isArray(detail)) {
    return `${status}: ${detail.map((problem) => (problem as { msg?: string }).msg ?? JSON.stringify(problem)).join("; ")}`;
  }
  return `${status}: ${fallback}`;
}

// The error a refused answer throws, with the server's words
async function readRefusal(response: Response): Promise<Error> {
  if (response.status === 401) window.dispatchEvent(new Event(SIGNED_OUT_EVENT));
  // a body that is no JSON leaves the status text to say why
  const body: unknown = await response.json().catch(() => null);
  return new Error(describeRefusal(response.status, body, response.statusText));
}

async function readAnswer<T>(response: Response): Promise<T> {
  if (response.ok) {
    try {
      return (await response.json()) as T;
    } catch (error) {
      // an answer that is no JSON (a proxy's page in place of the server's) is a failure, never the person's JSON
      if (error instanceof SyntaxError) throw new Error(`${response.status}: the server's answer was not readable`);
      throw error;
    }
  }
  throw await readRefusal(response);
}

// A file the server sent, saved by the browser under the name given
function saveBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  // the browser has the file once the click is handled; the address is let go a while after, never at once
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export async function fetchJson<T>(path: string): Promise<T> {
  return readAnswer<T>(await fetch(`/api${path}`));
}

export async function sendJson<T>(path: string, body: unknown = {}): Promise<T> {
  return readAnswer<T>(
    await fetch(`/api${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

// The settings profile and video workflow last chosen in the inbox's Run settings, kept in the page's view state so a
// video's own page starts from them
export const RUN_SETTINGS_CHOICE_KEY = "inbox.runSettingsChoice";
export interface RunSettingsChoice {
  profile: string;
  workflow?: string;
  speed_preset?: string;
  speed_settings?: Record<string, SpeedValue>;
}

// A done run's video and how far its compression to 720p is (the Manager tab's Compress videos)
export type VideoCompressionState = "not compressed" | "waiting" | "compressing" | "compressed" | "failed";
export interface CompressibleVideo {
  root: string;
  name: string;
  display_name: string;
  video: string;
  is_in_inbox: boolean;
  size_bytes: number;
  state: VideoCompressionState;
  progress: number;
  error: string;
  before_bytes: number;
  after_bytes: number;
}

export function fetchCompressibleVideos(): Promise<CompressibleVideo[]> {
  return fetchJson<CompressibleVideo[]>("/videos/compression");
}

export function startVideoCompression(runs: { root: string; name: string }[]): Promise<{ pid: number }> {
  return sendJson<{ pid: number }>("/videos/compression", { runs });
}

// The person's own lists in the run list: each a name and its rows, by the keys the page gives them
// (an inbox video's path, a run folder's key); a row stands in one list at most
export interface RunList {
  id: string;
  name: string;
  members: string[];
}

export async function fetchRunLists(): Promise<RunList[]> {
  return (await fetchJson<{ lists: RunList[] }>("/lists")).lists;
}

export async function saveRunLists(lists: RunList[]): Promise<RunList[]> {
  return (await putJson<{ lists: RunList[] }>("/lists", { lists })).lists;
}

// The lists with `keys` taken out of every other list and added to the list `target` names, where a key it holds
// already keeps its place, skipped with no word (the owner); no target leaves them in none
export function moveToList(lists: RunList[], keys: string[], target: string | null): RunList[] {
  const moved = new Set(keys);
  return lists.map((list) => {
    if (list.id !== target) return { ...list, members: list.members.filter((member) => !moved.has(member)) };
    return { ...list, members: [...list.members, ...[...moved].filter((key) => !list.members.includes(key))] };
  });
}

export async function putJson<T>(path: string, body: unknown): Promise<T> {
  return readAnswer<T>(
    await fetch(`/api${path}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

export async function deleteJson<T>(path: string): Promise<T> {
  return readAnswer<T>(await fetch(`/api${path}`, { method: "DELETE" }));
}

// What went wrong, in words a message can show: a thrown value need not be an Error (a rejected file read hands over
// whatever the browser gave it)
// A thrown value's words. Mermaid throws a plain object, which `String` wrote as "[object Object]" where the parse error
// belonged
export function describeError(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (error && typeof error === "object") {
    const { message, str } = error as { message?: unknown; str?: unknown };
    if (typeof message === "string") return message;
    if (typeof str === "string") return str;
    return JSON.stringify(error);
  }
  return String(error);
}

// One file sent as a form field, for an upload that needs no progress bar.
export async function sendFile<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  return readAnswer<T>(await fetch(`/api${path}`, { method: "POST", body: form }));
}

export function queueJobs(items: { video: string; output: string }[], selection: PhaseSelection): Promise<Job[]> {
  return sendJson<Job[]>("/jobs", { items, ...selection });
}

// A file to upload, and its path inside a dropped folder (just its name when it came alone).
export interface UploadEntry {
  file: File;
  path: string;
}

// Each file with its path inside what was dropped; a route that keeps no paths (the inbox upload) ignores them
function postFiles<T>(url: string, entries: UploadEntry[], onProgress: (fraction: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    entries.forEach((entry) => {
      form.append("files", entry.file, entry.file.name);
      form.append("paths", entry.path);
    });
    const request = new XMLHttpRequest();
    request.open("POST", url);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    request.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(request.responseText);
      } catch {
        // no JSON: a refusal says so in its own text, and an answer that should be the list is not readable
      }
      if (request.status < 200 || request.status >= 300) reject(new Error(describeRefusal(request.status, body, request.responseText)));
      else if (body === null) reject(new Error(`${request.status}: the server's answer was not readable`));
      else resolve(body as T);
    };
    request.onerror = () => reject(new Error("the upload did not reach the server"));
    request.onabort = () => reject(new Error("the upload was stopped before it arrived"));
    request.ontimeout = () => reject(new Error("the upload took too long and was stopped"));
    request.send(form);
  });
}

// XMLHttpRequest rather than fetch: it reports upload progress, which a recording of a gigabyte needs.
export function uploadFiles(entries: UploadEntry[], onProgress: (fraction: number) => void): Promise<UploadOutcome[]> {
  return postFiles("/api/inbox/upload", entries, onProgress);
}

// What an upload of several files, a folder or a zip did: each recording saved into the inbox as its own (the names the
// inbox lists), and each one not taken with why (seen before, or a transcript no recording of its name came with)
export interface UploadBundleOutcome {
  added: string[];
  refused: UploadOutcome[];
}

// Several files, a folder or a zip: every recording saved into the inbox as its own, with no question;
// recordings that are one meeting are merged afterwards with `mergeRecordings`.
export function uploadBundle(entries: UploadEntry[], onProgress: (fraction: number) => void): Promise<UploadBundleOutcome> {
  return postFiles("/api/inbox/upload-bundle", entries, onProgress);
}

// Two or more inbox recordings merged into one recording in parts; `name` empty takes the beginning their names share
export function mergeRecordings(videos: string[], name = ""): Promise<{ video: string; name: string }> {
  return sendJson("/inbox/merge", { videos, name });
}

interface DroppedEntry {
  isFile: boolean;
  isDirectory: boolean;
  fullPath: string;
  file?: (resolve: (file: File) => void, reject: (error: unknown) => void) => void;
  createReader?: () => { readEntries: (resolve: (entries: DroppedEntry[]) => void, reject: (error: unknown) => void) => void };
}

async function readDroppedEntry(entry: DroppedEntry): Promise<UploadEntry[]> {
  if (entry.isFile && entry.file) {
    const file = await new Promise<File>((resolve, reject) => entry.file!(resolve, reject));
    return [{ file, path: entry.fullPath.replace(/^\//, "") }];
  }
  if (!entry.isDirectory || !entry.createReader) return [];
  const reader = entry.createReader();
  const children: DroppedEntry[] = [];
  // a directory reader hands its entries over in batches until it returns an empty one
  for (;;) {
    const batch = await new Promise<DroppedEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) break;
    children.push(...batch);
  }
  return (await Promise.all(children.map(readDroppedEntry))).flat();
}

// What was dropped, folders opened: each file with its path inside the dropped folder.
export async function collectDroppedFiles(transfer: DataTransfer): Promise<UploadEntry[]> {
  const entries = Array.from(transfer.items ?? [])
    .map((item) => (typeof item.webkitGetAsEntry === "function" ? item.webkitGetAsEntry() as unknown as DroppedEntry | null : null))
    .filter((entry): entry is DroppedEntry => entry !== null);
  if (!entries.length) return Array.from(transfer.files).map((file) => ({ file, path: file.name }));
  return (await Promise.all(entries.map(readDroppedEntry))).flat();
}

export const NO_SELECTION: PhaseSelection = {
  only_phase: null,
  from_phase: null,
  to_phase: null,
  worker_indexes: [],
  is_resuming: false,
  is_dry_run: false,
  profile: "",
  tags: [],
  ingesting_profile: "",
  is_run_as_audio: false,
};

export interface RecordingCompression {
  // `stopped`: the record says running, and its process is gone
  status: "running" | "done" | "failed" | "stopped";
  error: string;
  before_bytes: number;
  after_bytes: number;
  files: number;
  // the share of the recording encoded so far, 0 to 1
  progress: number;
}

export interface Phase {
  number: number;
  name: string;
  title?: string;
  summary?: string;
  reads?: string;
  writes?: string;
  is_model_used?: boolean;
  // false when the phase's switch is off in the playbook files (`review.isOn`, `critiqueCollection.isOn`)
  is_on?: boolean;
}

export interface ArtifactFile {
  path: string;
  name: string;
  size_bytes: number;
  // a version of the document: what it is, as the Open document menu names it ("knowledge draft after part 1 of 5")
  label?: string;
  time_s?: number | null;
  // a PDF's page or figure: the page it is on, shown in place of a time
  page?: number;
  // phase 2's knowledge filter: 0 to 1, a frame at or above the classifier's threshold was dropped
  drop_score?: number;
  // phase 2's frame classifier: people (dropped, no knowledge), text (only text) or other (knowledge an OCR would lose)
  tag?: string;
  // a dropped frame: the layer that dropped it, filter (the knowledge filter), redundancy (the redundancy filter),
  // refine or hand; the redundancy filter's also the frames said to hold it and the model's reason
  dropped_by?: string;
  // the kind of empty frame the decision model named (liquidai-d1-2B): people, desktop, blank or joining
  drop_class?: string;
  covered_by?: string[];
  drop_reason?: string;
  is_hidden?: boolean;
}

// Fetches a zip of chosen run-folder files and hands it to the browser as a download.
export async function downloadZip(runBase: string, paths: string[], label: string): Promise<void> {
  const response = await fetch(`/api${runBase}/zip`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ paths, label }),
  });
  if (!response.ok) throw await readRefusal(response);
  saveBlob(await response.blob(), `${runBase.split("/").pop()}-${label}.zip`);
}

// Whether a video or a run matches a search: its name, its AI name or its topic holds the words typed, letter case
// ignored; an empty search finds everything
export function isRunFound(entry: { name: string; display_name?: string; topic?: string }, search: string): boolean {
  const needle = search.trim().toLowerCase();
  return !needle || [entry.name, entry.display_name ?? "", entry.topic ?? ""].some((text) => text.toLowerCase().includes(needle));
}

// Whether two names say the same, letter case and spacing aside: a topic equal to the name shown is not shown again
export function isSameName(first: string, second: string): boolean {
  const flatten = (text: string) => text.trim().replace(/\s+/g, " ").toLowerCase();
  return flatten(first) === flatten(second);
}

// What became of one run folder Rename was asked about: its name now, the name it is shown under, and "renamed" or why
// it kept its name ("kept: no AI name yet", a run writing into it, the new name taken)
export interface RenameRecord {
  name: string;
  display_name: string;
  outcome: string;
}

// Renames each run folder to its AI name (shown under the AI name as written) when `isByTopic`, else back to the name
// it had before
export function nameRuns(runs: { root: string; name: string }[], isByTopic: boolean): Promise<CommandOutput & { records: RenameRecord[] }> {
  return sendJson("/runs/display-names", { runs, is_by_topic: isByTopic });
}

// A part of a run a batch download may hold, with the words the page shows for it
export interface DownloadPart {
  key: string;
  label: string;
}

// Each run's document versions, oldest first, so a download asks which version when one run has several
export interface RunDocumentVersions { root: string; name: string; versions: DocumentVersion[] }
export function fetchDocumentVersions(runs: { root: string; name: string }[]): Promise<RunDocumentVersions[]> {
  return sendJson<RunDocumentVersions[]>("/download/document-versions", { runs });
}

// The ticked runs' chosen parts in one zip, saved by the browser; the answer names the runs left out (no document)
// `version`, when a run has several documents: the latest ("" or "latest"), the original, or all of them
export type DownloadVersion = "" | "latest" | "original" | "all";
export async function downloadDocuments(runs: { root: string; name: string }[], parts: string[],
                                        version: DownloadVersion = ""): Promise<string[]> {
  const response = await fetch("/api/download/documents", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ runs, parts, version }),
  });
  if (!response.ok) throw await readRefusal(response);
  saveBlob(await response.blob(), decodeURIComponent((response.headers.get("Content-Disposition") ?? "").split("''")[1] ?? "documents.zip"));
  const skipped = decodeURIComponent(response.headers.get("X-Skipped-Runs") ?? "");
  return skipped ? skipped.split(", ") : [];
}

// A phase's name as a reader sees it: `frames_processing` is shown "frames processing".
// The owner's words for a phase whose key is not what the page calls it (AGENTS.md's Vocabulary)
const PHASE_DISPLAY_NAMES: Record<string, string> = { finalize: "document writing" };

export function formatPhaseName(name: string): string {
  return PHASE_DISPLAY_NAMES[name] ?? name.replace(/_/g, " ");
}

// A run's state in the page's words, the same in every place that shows it: the Inbox's sections ("Not finished",
// "Done"), its pills and a run's header. The server's own words ("started", "incomplete", "complete") stay its own.
const RUN_STATE_WORDS: Record<string, string> = { started: "not finished", incomplete: "not finished", complete: "done" };

export function formatRunState(state: string): string {
  return RUN_STATE_WORDS[state] ?? state;
}

// A run's phase as its card names it: the title the run gives it for its medium, else its name.
export function formatPhaseTitle(phase: { name: string; title?: string }): string {
  return phase.title ?? formatPhaseName(phase.name);
}

export function formatSize(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`;
  return `${Math.max(1, Math.round(bytes / 1e3))} KB`;
}

// A moment the server wrote (`2026-10-01T13:05:00`) as the page shows it: `10-01 13:05`, with the year and the
// seconds where the place needs them
export function formatDateTime(iso: string, { withYear = false, withSeconds = false } = {}): string {
  const shown = iso.replace("T", " ");
  return shown.slice(withYear ? 0 : 5, withSeconds ? 19 : 16);
}

// `192` as `3m 12s`, `3840` as `1h 04m`: how long something took.
export function formatDuration(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `${whole}s`;
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  if (hours) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  return `${minutes}m ${String(whole % 60).padStart(2, "0")}s`;
}

// `419` as `6:59`, and past an hour `1:06:59`: a moment of the recording as a player shows it.
export function formatTime(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const rest = String(whole % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

export interface ArtifactGroup {
  key: string;
  title: string;
  // "text": a PDF's phase 1 text, its paragraphs searchable; "workers": each worker's turns, built by the page
  kind: "markdown" | "images" | "audio" | "text" | "workers";
  items: ArtifactFile[];
}

// The address of one run-folder file; `path` is relative to the run folder, each part encoded.
// A markdown artifact's versions, newest first: when each was kept and how large it was.
export type MarkdownVersion = { at: string; size_bytes: number };

export type MarkdownSaved = { path: string; size_bytes: number; kept_as: string };

// Saves one markdown artifact of a run folder as the page's editor left it; the server keeps the bytes it replaces.
export function saveMarkdown(runBase: string, path: string, markdown: string): Promise<MarkdownSaved> {
  return putJson<MarkdownSaved>(`${runBase}/files/${encodeFilePath(path)}`, { markdown });
}

// A run folder's file path as an address: each part encoded, the slashes kept
function encodeFilePath(path: string): string {
  return path.split("/").map(encodeURIComponent).join("/");
}

// The address a run folder's file is read from, shown in the page
export function buildFileUrl(runBase: string, path: string): string {
  return `/api${runBase}/files/${encodeFilePath(path)}`;
}

// The address the same file is saved from: the browser downloads it under its own name instead of showing it
export function buildDownloadUrl(runBase: string, path: string): string {
  return `/api${runBase}/download/${encodeFilePath(path)}`;
}

// One version of a run's published document, numbered from 1, with its path inside the run folder
export interface DocumentVersion {
  number: number;
  label: string;
  path: string;
}

// A version of the published document, or every version without a number, as a zip with its figures
export function buildVersionsDownloadUrl(runBase: string, number?: number): string {
  return `/api${runBase}/document-versions/download${number ? `?number=${number}` : ""}`;
}

export interface TrashEntry {
  name: string;
  is_folder: boolean;
  size_bytes: number;
  // the inbox video path its runs name; "" when it was moved before that was recorded
  video: string;
  // for a run folder moved whole, the path it came from; "" for a video
  run_folder: string;
}

// An archived video: the trash's fields, with the runs that name it and its state by the inbox's own rule.
export interface ArchiveEntry extends TrashEntry {
  runs: InboxItem["runs"];
  state: InboxItem["state"];
  topic: string;
}

// One setting as `/api/settings` lists it: its key as the playbook files write it, the value in force, which file set
// it ("code" for the program's own default), the kind of editor it takes, and the comment above it in config.py
export interface SettingRow {
  key: string;
  value: unknown;
  // "long-text": a text of several lines, such as an LLM pass's extra wishes, edited in a box of its own
  kind: "bool" | "number" | "text" | "long-text" | "json";
  source: string;
  comment: string;
  // The values the setting's type names; the page offers them as a list
  choices?: string[];
  // The key part by part: a model's name may hold a dot, so the key is never split at its dots
  path?: string[];
  // The options of a setting chosen from a list the program builds (the configured models, the models each host
  // serves, the hosts' addresses), each shown by its label, with a hint under it, under its group's heading
  choice_options?: SettingOption[] | null;
  // Whether a value of one's own may be typed beside the list (a model not on a host yet, a new host's address)
  is_open_choice?: boolean;
  // A model's spec only: the address the model is called at, and the one an empty `baseUrl` means
  chosen_address?: string;
  default_address?: string;
}

export interface SettingOption {
  value: string;
  label: string;
  hint: string;
  // the heading the option stands under ("LM Studio at http://localhost:1234/v1"), "" for none
  group: string;
  // a host's model: the address of the host serving it
  address?: string;
}

// One phase 3 batch or follow-up, read from the run's logs: what it cost, what came of it, and why an answer was refused
// One phase 4 step of a run folder, for the document writing card's table (`GET <run>/document-steps`)
export interface DocumentStep {
  slug: string;
  stage: string;
  step: string;
  status: string;
  calls: number;
  seconds: number;
  prompt_tokens: number;
  output_tokens: number;
  rejections: { findings: { code: string; text: string }[] }[];
}

// What the worker of one phase saw: `GET …/worker?phase=N` lists its steps, each with the attempts
// the run saved; `GET …/worker/attempt?ref=` opens one
export interface WorkerAttemptRef {
  ref: string;
  label: string;
  session: string;
  status: string;
}

export interface WorkerStep {
  step: string;
  kind: "call" | "conversation";
  is_dry_run: boolean;
  phase_folder?: string;
  attempts: WorkerAttemptRef[];
}

export type WorkerInputSegment =
  | { kind: "text"; text: string }
  | { kind: "image"; frame: number | null; path: string | null };

export interface WorkerTurn {
  kind: "turn" | "result" | "blocked" | "accepted" | "compacted";
  thinking?: string;
  said?: string;
  calls?: { name: string; args: unknown }[];
  prompt_tokens?: number | null;
  tool?: string;
  result?: string;
  text?: string;
  at?: string | null;
}

export interface WorkerAttempt {
  kind: "call" | "conversation";
  instructions: string | null;
  input: WorkerInputSegment[];
  findings: { code: string; detail: string }[];
  turns: WorkerTurn[];
}

export interface InstructionPreview {
  name: string;
  // who is sent it, at which stage of which phase
  used_by: string;
  text: string;
  error: string;
}

export interface BatchHistoryEntry {
  slug: string;
  frames: string;
  step: string;
  started_at: string;
  duration_s: number;
  status: string;
  attempts: number;
  prompt_tokens: number;
  output_tokens: number;
  rejections: { attempt: number; findings: { code: string; detail: string }[] }[];
}

// A run folder's path from the repository root, `output/<name>`: how the page names one run folder everywhere it keeps
// or compares one (the open run, the runs a video owns, a move's run folders)
export function buildRunPath(run: { root: string; name: string }): string {
  return `${run.root}/${run.name}`;
}

// The API address of one run folder, which every request about that run starts with
export function buildRunBase(run: { root: string; name: string }): string {
  return `/runs/${encodeURIComponent(run.root)}/${encodeURIComponent(run.name)}`;
}

// What a reset printed, read once for every page that resets: its refusal (the first line, or the exit code when it
// printed nothing), else how many files and records it removed (`reset-to` lists each one indented under its heading)
export function readResetOutput(answer: CommandOutput): { refusal: string; removedCount: number } {
  const lines = answer.output.trim().split("\n");
  if (answer.exit_code !== 0) return { refusal: lines[0] || `exit code ${answer.exit_code}`, removedCount: 0 };
  return { refusal: "", removedCount: lines.filter((line) => line.startsWith("  ")).length };
}

// Renames an inbox video no run used yet; the answer is its new path and name
export function renameInboxVideo(video: string, name: string): Promise<{ video: string; name: string }> {
  return sendJson<{ video: string; name: string }>("/inbox/rename", { video, name });
}

// Every tag an inbox video is to have before its first run; the server keeps them so its page shows them after a refresh
export function saveInboxTags(video: string, tags: string[]): Promise<{ video: string; tags: string[] }> {
  return putJson<{ video: string; tags: string[] }>("/inbox/tags", { video, tags });
}

// Sets every tag a run is to have, as `i2k tags --set` does; the answer says what it now has
// The name a run is shown under, from the pencil beside its title; "" shows its folder's name again
export function saveRunDisplayName(runBase: string, name: string): Promise<CommandOutput> {
  return putJson<CommandOutput>(`${runBase}/display-name`, { name });
}

export function saveRunTags(runBase: string, tags: string[]): Promise<CommandOutput> {
  return putJson<CommandOutput>(`${runBase}/tags`, { tags });
}

// Saves one setting in both default settings files, or in a profile's file; the answer says what was written, or why not
export function saveSetting(key: string, newValue: unknown, profile: string, keyParts?: string[]): Promise<CommandOutput> {
  return putJson<CommandOutput>("/settings", { key, new_value: newValue, profile, key_parts: keyParts ?? null });
}

// What the setup wizard shows (`GET /api/setup`): each model host, API and tool a run needs, each model a phase calls,
// and the models download. A run needs the one host its models name; `kind` tells a local host, an API and a tool apart.
export type SetupSystem = "mac" | "windows" | "linux";

export interface SetupRuntime {
  key: string;
  name: string;
  kind: "host" | "api" | "tool";
  purpose: string;
  download_url: string;
  key_variable: string;
  steps: Record<SetupSystem, string[]>;
  start_steps: string[];
  is_needed: boolean;
  // found on this machine; for an API, its key is set
  is_installed: boolean;
  // a local host's server answers; null for an API or a tool
  is_running: boolean | null;
  // the models a running host has; empty for an API, a tool or a host that does not answer
  available_models: string[];
  // a tool that runs a model of its own (the scanned-page reader): that model; "" for every other
  model_id?: string;
}

export interface SetupModel {
  name: string;
  spec: string;
  used_by: string[];
  is_configured: boolean;
  // null while its host does not answer, and for an API
  is_on_host: boolean | null;
  recommended_repository: string;
  is_downloaded: boolean;
}

export interface SetupCheck {
  has_models: boolean;
  runtimes: SetupRuntime[];
  models: SetupModel[];
  download: { is_running: boolean; log: string };
  recommended: { repository: string; spec: string; used_by: string }[];
}

// Accounts and roles: the server asks nobody to sign in until an account exists.
export interface Account {
  name: string;
  role: string;
}

export interface AccountRow extends Account {
  created: string;
}

export interface AuthState {
  has_accounts: boolean;
  account: Account | null;
  roles: string[];
  // whether this server asks anyone to sign in: chosen when it starts, false with no account
  is_sign_in_required?: boolean;
}

export function fetchAuth(): Promise<AuthState> {
  return fetchJson<AuthState>("/auth/me");
}

export function signIn(name: string, password: string): Promise<Account> {
  return sendJson<Account>("/auth/login", { name, password });
}

export function signOut(): Promise<{ is_signed_out: boolean }> {
  return sendJson<{ is_signed_out: boolean }>("/auth/logout");
}

export async function listAccounts(): Promise<AccountRow[]> {
  return (await fetchJson<{ accounts: AccountRow[] }>("/accounts")).accounts;
}

export async function addAccount(name: string, password: string, role: string): Promise<AccountRow[]> {
  return (await sendJson<{ accounts: AccountRow[] }>("/accounts", { name, password, role })).accounts;
}

export async function updateAccount(name: string, change: { role?: string; password?: string }): Promise<AccountRow[]> {
  const response = await fetch(`/api/accounts/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(change),
  });
  return (await readAnswer<{ accounts: AccountRow[] }>(response)).accounts;
}

export async function removeAccount(name: string): Promise<AccountRow[]> {
  const response = await fetch(`/api/accounts/${encodeURIComponent(name)}`, { method: "DELETE" });
  return (await readAnswer<{ accounts: AccountRow[] }>(response)).accounts;
}

export function isAdmin(account: Account | null | undefined): boolean {
  // with no account at all, the page is this machine's own, as before accounts existed
  return !account || account.role === "admin";
}


// Projects and groups: a project gives roles to accounts and to groups; a run is in one project.
export interface Project {
  slug: string;
  title: string;
  runs: number;
  // the role the signed-in account holds there; null on a server with sign-in off
  role: string | null;
  // an admin's view alone: the roles the project gives by account and by group
  members?: Record<string, string>;
  groups?: Record<string, string>;
}

export interface Group {
  name: string;
  accounts: string[];
}

export interface ProjectsState {
  projects: Project[];
  groups: Group[];
}

export function fetchProjects(): Promise<ProjectsState> {
  return fetchJson<ProjectsState>("/projects");
}

async function sendProjects(method: string, path: string, body?: unknown): Promise<ProjectsState> {
  const response = await fetch(`/api${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return readAnswer<ProjectsState>(response);
}

const part = encodeURIComponent;

export const addProject = (title: string) => sendProjects("POST", "/projects", { title });
export const renameProject = (slug: string, title: string) => sendProjects("PUT", `/projects/${part(slug)}`, { title });
export const removeProject = (slug: string) => sendProjects("DELETE", `/projects/${part(slug)}`);
export const setProjectMember = (slug: string, account: string, role: string | null) =>
  sendProjects("PUT", `/projects/${part(slug)}/members/${part(account)}`, { role });
export const setProjectGroup = (slug: string, group: string, role: string | null) =>
  sendProjects("PUT", `/projects/${part(slug)}/groups/${part(group)}`, { role });
export const moveRunToProject = (slug: string, run: string) => sendProjects("PUT", `/projects/${part(slug)}/runs/${part(run)}`);
export const addGroup = (name: string) => sendProjects("POST", "/groups", { name });
export const setGroupAccounts = (name: string, accounts: string[]) => sendProjects("PUT", `/groups/${part(name)}`, { accounts });
export const removeGroup = (name: string) => sendProjects("DELETE", `/groups/${part(name)}`);
