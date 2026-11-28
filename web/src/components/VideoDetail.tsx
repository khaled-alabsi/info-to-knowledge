import { useEffect, useRef, useState } from "react";
import { choosePdfMode, describeError, describePdfFlow, fetchJson, formatSize, KNOWLEDGE_FILTER_MODELS, NO_SELECTION, PDF_PROCESSINGS, PDF_SCAN_ENGINES,
  PDF_STANDING_SHAPINGS, queueJobs, renameInboxVideo, RUN_SETTINGS_CHOICE_KEY, saveInboxTags } from "../api";
import type { InboxItem, Job, KnowledgeFilterModel, Phase, PhaseSelection, RunSettingsChoice } from "../api";
import { readViewValue } from "../viewState";
import { countJobsAhead, describeQueued } from "../jobs";
import { Dropdown } from "./Dropdown";
import Icon from "./Icon";
import PartsPlayer from "./PartsPlayer";
import PhaseSelector, { describePhases } from "./PhaseSelector";
import RangeEditor, { buildInboxPreviewSource, describeRange } from "./RangeEditor";
import type { RangeValue } from "./RangeEditor";
import { describeDefault, describeModels, PdfChoices, useIngestingProfiles, usePhaseModels, useRunDefaults }
  from "./RunChoices";
import WorkflowSpeedChoice, { readSpeedOffPhases, readSpeedWorkflow } from "./WorkflowSpeedChoice";
import { nameProviderSet, useProviderSets } from "./ProviderSetChoice";
import { RunTags } from "./RunTags";

// The page of an inbox video no run has used yet: the detail view before its run, so a click on a new video opens a page.
// It plays the video under a Recording tab and starts its run; once the run's folder shows, the app shows the run
// in its place. A video already sent to the queue says so and opens the job's console. Before the run, the person picks
// how a video is processed (as a video, or its sound alone as audio), and, for an audio run only, the ingesting profile its
// document is written in; "Automatic" leaves the profile to a tag or phase 2's own pick.
// What an untouched choice box says: the run is sent no value, and the settings (or the chosen profile) decide
// The workflow's three choices: the settings' own, or one for this run
// a Word or OpenDocument file, the office workflow's input
const OFFICE_NAME = /\.(docx|odt)$/i;
// a slide deck, the PPT workflow's input
const SLIDES_NAME = /\.(pptx|odp)$/i;

// What "More options" holds, in words: the phases, the settings and the models
function describeMoreOptions(selection: PhaseSelection): string {
  return [describePhases(selection), selection.profile ? `profile ${selection.profile}` : "default settings",
    describeModels(selection)].join(" · ");
}

// What the ⚙ beside Start run holds when nothing in it was changed; anything else shows on the button
const DEFAULT_MORE_OPTIONS = "every phase · default settings · default models";

export default function VideoDetail({
  item,
  phases,
  jobs,
  onBack,
  onOpenJob,
  onChanged,
  onMessage,
  onRenamed = () => {},
}: {
  item: InboxItem;
  phases: Phase[];
  jobs: Job[];
  onBack: () => void;
  onOpenJob: (job: Job) => void;
  onChanged: () => void;
  onMessage: (text: string, isError?: boolean) => void;
  // the video under its new name, so the page follows it to its new path
  onRenamed?: (item: InboxItem) => void;
}) {
  // The tags given here before (kept by the server), so a refresh shows them again
  // it starts from the settings profile and Workflow speed chosen in the inbox's Run settings
  const [selection, setSelection] = useState<PhaseSelection>(() => {
    const saved = readViewValue<RunSettingsChoice>(RUN_SETTINGS_CHOICE_KEY, { profile: "" });
    return { ...NO_SELECTION, tags: item.tags ?? [], ...(saved.profile ? { profile: saved.profile } : {}),
      ...(saved.speed_preset ? { speed_preset: saved.speed_preset, speed_settings: saved.speed_settings ?? {} } : {}) };
  });
  // The title turns into a name box from the pencil beside it; Enter renames, Escape leaves it as it was
  const [nameDraft, setNameDraft] = useState<string | null>(null);
  // the PDF menus' plain-words explanation, opened by its help button
  const [isPdfHelpOpen, setIsPdfHelpOpen] = useState(false);
  // the Range editor, opened by its button; the player's second, and a second asked of it
  const [isRangeOpen, setIsRangeOpen] = useState(false);
  const [playerS, setPlayerS] = useState(0);
  const [seekTo, setSeekTo] = useState<number | null>(null);
  const rename = async () => {
    if (nameDraft === null || nameDraft.trim() === "" || nameDraft === item.name) {
      setNameDraft(null);
      return;
    }
    try {
      const answer = await renameInboxVideo(item.video, nameDraft);
      onMessage(`Renamed ${item.name} to ${answer.name}`);
      setNameDraft(null);
      onRenamed({ ...item, video: answer.video, name: answer.name, path: item.path.replace(/[^/]+$/, answer.name) });
      onChanged();
    } catch (problem) {
      onMessage(`${item.name} was not renamed: ${describeError(problem)}`, true);
    }
  };
  // Every change of the tags is saved at once, so the page shows them again after a refresh; the tags go with the run
  // when it starts
  const saveTags = async (tags: string[]) => {
    await saveInboxTags(item.video, tags);
    setSelection((current) => ({ ...current, tags }));
  };
  // the control the keyboard comes back to when the name box goes away
  const renameButton = useRef<HTMLButtonElement>(null);
  const [isStarting, setIsStarting] = useState(false);
  // The phases of the flow chosen here, titled for it and with the ones it never runs off: an audio run's phases 2 and 3
  // are other work, and it has no review and no critique collection (the owner: "audio has different phases")
  const [flowPhases, setFlowPhases] = useState<Phase[] | null>(null);
  const runDefaults = useRunDefaults(selection.profile);
  // the workflow the chosen Workflow speed takes, for the phases listed
  const speedWorkflow = readSpeedWorkflow(selection, runDefaults);
  // a Word or OpenDocument file is converted to markdown with no model: it takes the PDF's
  // markdown conversion from phase 2 on, and has no pages, times or frames to choose
  const isOffice = OFFICE_NAME.test(item.name);
  // a deck's slides are read as pictures, as a PDF's pages as images
  const isSlides = SLIDES_NAME.test(item.name);
  const flowMedium = isOffice ? describePdfFlow("markdown-conversion") : isSlides ? describePdfFlow("pages-as-images")
    : item.name.toLowerCase().endsWith(".pdf")
    ? describePdfFlow(selection.pdf_mode || choosePdfMode(runDefaults, item.page_count))
    : item.is_audio || selection.is_run_as_audio ? "audio" : "video";
  // phase 7 shows only while a PDF's clean up or rewrite is chosen
  const flowPass = flowMedium.startsWith("pdf-") ? selection.document_pass ?? "" : "";
  // the phases the chosen speed switches off (Balanced has no review and no critique collection), a video's only
  const speedOff = flowMedium === "video" ? readSpeedOffPhases(selection, runDefaults) : {};
  const speedOffKey = JSON.stringify(speedOff);
  useEffect(() => {
    const query = new URLSearchParams({ medium: flowMedium, workflow: speedWorkflow,
      ingesting_profile: selection.ingesting_profile ?? "", ...(flowPass ? { document_pass: flowPass } : {}),
      ...JSON.parse(speedOffKey) });
    fetchJson<Phase[]>(`/phases?${query}`).then(setFlowPhases).catch(() => setFlowPhases(null));
  }, [flowMedium, speedWorkflow, selection.ingesting_profile, flowPass, speedOffKey]);
  // The ingesting profiles with what each is for; none answered shows only Automatic
  const providerSets = useProviderSets();
  const { phaseModels, error: modelsError } = usePhaseModels();
  const { profiles, error: profilesError } = useIngestingProfiles();
  const isPdf = item.video.toLowerCase().endsWith(".pdf");
  // a document of either kind has no frames to filter and no sound to process alone
  const isDocument = isPdf || isOffice || isSlides;
  const isAudioRun = item.is_audio || selection.is_run_as_audio;
  // A video run writes its document from its frames, so the profile is chosen only for audio (the owner); back to a
  // video, the choice is dropped rather than left chosen and hidden
  const setProcessAs = (isAsAudio: boolean) => setSelection({
    ...selection, is_run_as_audio: isAsAudio, ingesting_profile: isAsAudio ? selection.ingesting_profile : "",
    // an audio run takes the audio workflow: a video workflow chosen before is dropped
    workflow: isAsAudio ? "" : selection.workflow,
  });
  const job = jobs.find((candidate) => candidate.request.video === item.video
    && (candidate.status === "queued" || candidate.status === "running"));
  const parts = item.parts.length ? item.parts : [{ name: item.name, size_bytes: item.size_bytes }];
  const range: RangeValue = { range_start: selection.range_start ?? "", range_end: selection.range_end ?? "",
    range_pages: selection.range_pages ?? "" };
  const pageCount = isPdf ? item.page_count ?? 0 : 0;
  const durationS = item.duration_s ?? 0;
  const isRangeChosen = Boolean(range.range_start || range.range_end || range.range_pages);

  const start = async () => {
    setIsStarting(true);
    try {
      const ahead = countJobsAhead(jobs);
      await queueJobs([{ video: item.video, output: item.output }], selection);
      onMessage(describeQueued([item.name], ahead, "its run opens here once it appears"));
      onChanged();
    } catch (problem) {
      onMessage(`${item.name} did not start: ${describeError(problem)}`, true);
    } finally {
      setIsStarting(false);
    }
  };

  return (
    <div className="detail">
      <header className="detail-head video-head">
        <button className="btn ghost icon-only back" onClick={onBack} aria-label="back"><Icon name="back" /></button>
        <div className="detail-title">
          {nameDraft === null ? (
            <div className="title-row">
              <h2 className="truncate" title={item.name}>{item.name}</h2>
              {!job && (
                <button ref={renameButton} className="btn ghost icon-only small" aria-label="rename" title="rename the video in the inbox"
                  onClick={() => setNameDraft(item.name)}><Icon name="pencil" size={14} /></button>
              )}
              {/* the page's one action stays on the title's row, at its right; its options are the row below */}
              {!job && (
                <div className="split start-run">
                  <button className="btn primary" disabled={isStarting} onClick={() => void start()}>
                    {isStarting ? <><span className="spinner" aria-hidden="true" /> Starting…</> : <><Icon name="play" /> Start run</>}
                    {!isStarting && describeMoreOptions(selection) !== DEFAULT_MORE_OPTIONS
                      && <small className="run-settings"> · {describeMoreOptions(selection)}</small>}
                  </button>
                  {/* the phases, the settings and the models, changed less often, behind the ⚙ of the button they start,
                      as in the inbox */}
                  <Dropdown label={<Icon name="settings" size={16} />} variant="primary" align="right"
                    title="phases, settings and models" panelClassName="wide">
                    {() => <PhaseSelector phases={flowPhases ?? phases} phaseModels={phaseModels}
                      selection={selection} onChange={setSelection} isTagsShown={false} isProviderSetShown={false} />}
                  </Dropdown>
                </div>
              )}
            </div>
          ) : (
            <input type="text" className="title-input" aria-label="new name" value={nameDraft} autoFocus
              onChange={(event) => setNameDraft(event.target.value)} onBlur={() => void rename()}
              onKeyDown={(event) => {
                if (event.key === "Enter") void rename();
                if (event.key === "Escape") {
                  setNameDraft(null);
                  // the box goes; the keyboard comes back to the pencil that opened it
                  window.requestAnimationFrame(() => renameButton.current?.focus());
                }
              }} />
          )}
          <div className="title-meta">
          <small className="muted truncate" title={item.path}>
            {item.path} · {formatSize(item.size_bytes)}{item.parts.length > 1 ? ` · ${item.parts.length} parts` : ""}
          </small>
          {!job && <RunTags tags={selection.tags} save={saveTags} />}
          </div>
          {/* the options row inside the title's column, so it starts where the title starts */}
          <div className="run-actions">
          {job ? (
            <button className={`pill link ${job.status}`} title={`job ${job.job_id}: open its console`} onClick={() => onOpenJob(job)}>
              {job.status === "running" ? "starting…" : "queued"} ›
            </button>
          ) : (
            <>
              {/* the part of the input the run processes: the whole by default, its editor opened below the header */}
              {!isOffice && !isSlides && (
                <button className={`btn field-button range-button${isRangeChosen ? " chosen" : ""}`} aria-expanded={isRangeOpen}
                  aria-controls="range-editor" title="process only a part of the input"
                  onClick={() => setIsRangeOpen(!isRangeOpen)}>
                  <span className="btn-label"><Icon name="range" size={14} /> <small>Range</small>
                    {" "}{describeRange(range, durationS, pageCount)}</span>
                </button>
              )}
              {isOffice && (
                <p className="muted office-note">Converted to markdown with no model, tables and pictures kept. Once it is
                  done, Change this document offers a clean up, a rewrite or a knowledge reshaping by a model.</p>
              )}
              {isSlides && (
                <p className="muted office-note">Each slide is drawn as a picture and read by the vision model, never as
                  its text; the speaker notes are what is said over it. Hidden slides are left out.</p>
              )}
              {/* A PDF's processing mode, named by what the model reads: each page as a picture, read in batches, or its text
                  only, with no picture */}
              {/* each box names its choice, or "settings default" while untouched: the run then takes the settings'
                  value, and the box never names one it does not send */}
              {isPdf && <PdfChoices selection={selection} onChange={setSelection} defaults={runDefaults} pageCount={item.page_count} isBoxed />}
              {isPdf && (
                <button className="btn ghost icon-only" aria-label="explain the PDF options" aria-expanded={isPdfHelpOpen}
                  onClick={() => setIsPdfHelpOpen(!isPdfHelpOpen)}>
                  <Icon name="help" />
                </button>
              )}
              {isPdf && isPdfHelpOpen && (
                <div className="field-help" role="note" aria-label="the PDF options explained">
                  <p><strong>Processing</strong>: how the PDF is processed.</p>
                  <ul>{PDF_PROCESSINGS.map((entry) => <li key={entry.value}><strong>{entry.label}</strong>: {entry.hint}.</li>)}</ul>
                  <p><strong>Document</strong>: how the final document is made; its list follows the processing.</p>
                  <ul>{PDF_STANDING_SHAPINGS.map((entry) => <li key={entry.value}><strong>{entry.label}</strong>: {entry.hint}.</li>)}
                    <li><strong>A profile</strong> (knowledge, meeting, ...): a model writes the document in that shape.</li></ul>
                  <p><strong>Scans</strong>: what reads a scanned page (a picture of a page) before any model reads it; a
                    page with text is read as it is.</p>
                  <ul>{PDF_SCAN_ENGINES.map((choice) => <li key={choice.value}><strong>{choice.label}</strong>: {choice.hint}.</li>)}</ul>
                  <p><strong>default</strong> in a list: a small PDF is read as pages as images, a large one kept as it
                    stands, any other read as text and written as knowledge; the Settings tab sets the sizes and the scans.</p>
                </div>
              )}
              {/* Which model drops a video's frames with no knowledge in phase 2 */}
              {!item.is_audio && !isDocument && !selection.is_run_as_audio && (
                <label className="field-select"
                  title={`Knowledge filter: ${KNOWLEDGE_FILTER_MODELS.map((model) => `${model.label}: ${model.hint}`).join("; ")}`}>
                  <small>Knowledge filter</small>
                  <select aria-label="knowledge filter model" value={selection.knowledge_filter_model ?? ""}
                    onChange={(event) => setSelection({ ...selection,
                      knowledge_filter_model: event.target.value as "" | KnowledgeFilterModel })}>
                    <option value="">{describeDefault(runDefaults?.knowledge_filter_model)}</option>
                    {KNOWLEDGE_FILTER_MODELS.map((model) => <option key={model.value} value={model.value}>{model.label}</option>)}
                  </select>
                </label>
              )}
              {/* each choice is one box, its name inside it with its buttons, as the Knowledge filter's: a name apart
                  from its buttons wrapped onto its own line (the owner: the buttons' "order position" can be better) */}
              {!item.is_audio && !isDocument && (
                <div className="field-toggle choice-process">
                  <small>Process</small>
                  <div className="segmented toggle-pair" role="radiogroup" aria-label="process as"
                    title="Video reads its frames and its speech; Audio only its sound, with no frame taken">
                    {([false, true] as const).map((isAudio) => (
                      <button key={String(isAudio)} role="radio" aria-checked={Boolean(selection.is_run_as_audio) === isAudio}
                        className={Boolean(selection.is_run_as_audio) === isAudio ? "on" : ""} onClick={() => setProcessAs(isAudio)}>
                        {isAudio ? "Audio only" : "Video"}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {/* A video's Workflow speed: a preset (Quality, Balanced, Fast) or its settings changed by hand */}
              {!isAudioRun && !isDocument && (
                <div className="field-toggle choice-workflow">
                  <small>Workflow speed</small>
                  <WorkflowSpeedChoice selection={selection} onChange={setSelection} defaults={runDefaults} />
                </div>
              )}
              {/* Which provider set runs it, in the row itself: behind Start run's gear the owner never found it */}
              {providerSets && providerSets.sets.length > 1 && (
                <label className="field-select" title="Provider set: the machine and models this run uses">
                  <small>Provider set</small>
                  <select aria-label="provider set" value={selection.provider_set ?? ""}
                    onChange={(event) => setSelection({ ...selection, provider_set: event.target.value })}>
                    <option value="">{nameProviderSet(providerSets.sets.find((entry) => entry.name === providerSets.default_set)
                      ?? { name: providerSets.default_set })} (default)</option>
                    {providerSets.sets.filter((entry) => entry.name !== providerSets.default_set).map((entry) => (
                      <option key={entry.name} value={entry.name}>{nameProviderSet(entry)}</option>
                    ))}
                  </select>
                </label>
              )}
              {isAudioRun && (
                <label className="field-select" title="Ingesting profile: the shape of the document; automatic is a tag, else phase 2 picks one">
                  <small>Profile</small>
                  <select aria-label="ingesting profile" value={selection.ingesting_profile}
                    onChange={(event) => setSelection({ ...selection, ingesting_profile: event.target.value })}>
                    <option value="">automatic profile</option>
                    {profiles.map((profile) => (
                      <option key={profile.name} value={profile.name} title={profile.description}>{profile.name}</option>
                    ))}
                  </select>
                </label>
              )}
              {(modelsError || profilesError) && (
                <small className="error">{[modelsError, profilesError].filter(Boolean).join("; ")}</small>
              )}
            </>
          )}
          </div>
        </div>
      </header>

      {isRangeOpen && !job && (
        <div id="range-editor">
          <RangeEditor source={buildInboxPreviewSource(item.video)} durationS={durationS} pageCount={pageCount} isAudio={isAudioRun}
            value={range} onChange={(next) => setSelection({ ...selection, ...next })}
            playerS={playerS} onSeek={setSeekTo} onClose={() => setIsRangeOpen(false)} />
        </div>
      )}
      {/* a Word or OpenDocument file has nothing a browser plays or shows: no Recording tab for it */}
      {!isOffice && !isSlides && (
        <>
          <div className="view-tabs" role="tablist">
            <button role="tab" aria-selected className="on">Recording</button>
          </div>
          <PartsPlayer className="card recording" parts={parts} seekTo={seekTo} onSeeked={() => setSeekTo(null)}
            isSeekPlaying={false} onTimeUpdate={setPlayerS}
            buildSrc={(index) => `/api/inbox/video?path=${encodeURIComponent(item.video)}${index ? `&part=${index}` : ""}`} />
        </>
      )}
    </div>
  );
}
