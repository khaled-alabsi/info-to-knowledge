import { useEffect, useState } from "react";
import type { DocumentPass, DocumentSource, IngestingProfile, Phase, PhaseModels, PhaseSelection, RunDefaults } from "../api";
import type { PdfProcessing } from "../api";
import { buildPdfChoice, chooseDefaultShaping, choosePdfMode, describeDocumentPass, describeError, describePdfShaping,
  DOCUMENT_PASSES,
  fetchJson, listPdfProfiles, openInstruction, PDF_PROCESSINGS, PDF_SCAN_ENGINES, PDF_STANDING_SHAPINGS,
  readPdfProcessing, readPdfShaping } from "../api";
import { PanelNote } from "./Dropdown";
import Icon from "./Icon";

// What a run is started with, beside which phases it runs: the settings files it reads, and the model each phase calls.
// The Phases menu shows them under "Run with"; a new video's page shows them in its toolbar instead.

// A configured model as the person knows it: the model's own name from its spec, `lmstudio:lfm2.5-vl-3b` -> lfm2.5-vl-3b
function readModelName(spec: string): string {
  return spec.slice(spec.indexOf(":") + 1);
}

// One phase's choices: the model it calls by default first, then every other model once, whatever entry names it
function listModelChoices(phaseModels: PhaseModels, defaultName: string): { value: string; label: string }[] {
  const defaultSpec = phaseModels.models.find((model) => model.name === defaultName)?.spec ?? defaultName;
  const choices = [{ value: "", label: `${readModelName(defaultSpec)} (default)` }];
  const seen = new Set([defaultSpec]);
  for (const model of phaseModels.models) {
    if (seen.has(model.spec)) continue;
    seen.add(model.spec);
    choices.push({ value: model.name, label: readModelName(model.spec) });
  }
  return choices;
}

// One choice as a labelled drop-down list, with a "default" pill beside it while the run keeps the default: in the run
// settings dialog, where a list of every value as buttons read as old-fashioned (the owner: "make it a drop-down list
// ... the default keyword can be a pill")
export function ChoiceDropdown({ label, choices, value, isDefault, onChange, title, help }: {
  label: string;
  choices: { value: string; label: string; isDisabled?: boolean }[];
  value: string;
  isDefault: boolean;
  onChange: (value: string) => void;
  title?: string;
  // what the choice is and what each option does, in plain words, opened by the ? beside the list (the owner: "what is
  // PYM? I don't know, those stuff")
  help?: { intro: string; options: { label: string; text: string }[] };
}) {
  const [isHelpOpen, setIsHelpOpen] = useState(false);
  return (
    <div className="choice-dropdown" title={title}>
      <label className="choice-label" htmlFor={`choice-${label}`}>{label}</label>
      <span className="choice-row">
        <select id={`choice-${label}`} aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>
          {choices.map((choice) => (
            <option key={choice.value} value={choice.value} disabled={choice.isDisabled}>{choice.label}</option>
          ))}
        </select>
        {help && (
          <button type="button" className="btn ghost icon-only small help-button" aria-expanded={isHelpOpen}
            aria-label={`what ${label} means`} title={`what ${label} means`} onClick={() => setIsHelpOpen(!isHelpOpen)}>
            <Icon name="help" size={16} />
          </button>
        )}
        {isDefault && <span className="pill default-pill">default</span>}
      </span>
      {help && isHelpOpen && (
        <div className="choice-help" role="note" aria-label={`${label} explained`}>
          <p>{help.intro}</p>
          <dl>
            {help.options.map((option) => (
              <div key={option.label}><dt>{option.label}</dt><dd>{option.text}</dd></div>
            ))}
          </dl>
        </div>
      )}
    </div>
  );
}

// The help each choice of the run settings dialog opens: what it is, and what each option does, in plain words
export const CHOICE_HELP = {
  pdfProcessing: {
    intro: "How the PDF is processed before its document is made. By default a small PDF is read as pages as images, "
      + "any other as text.",
    options: [
      { label: "Text", text: "The PDF's text is read, and its figures are cut out of the pages. Fast." },
      { label: "Pages as images", text: "A model looks at each page as a picture, a few pages at a time. Slowest; for "
          + "PDFs whose layout or pictures carry the meaning." },
    ],
  },
  pdfShaping: {
    intro: "How the final document is made. Every choice first reads the PDF's text; they differ in what happens next. "
      + "The Markdown choices need Text processing. By default a large PDF gets Markdown, any other an LLM-written "
      + "document.",
    options: [
      { label: "Markdown", text: "Step 2: the program writes the PDF's text out as markdown, each figure where it stood. "
          + "No model. You get: the PDF as it is, in markdown. Seconds to minutes." },
      { label: "Markdown + LLM clean up", text: "Step 2: Markdown, as above. Step 3: a model takes out what a reader "
          + "does not need (a wiki's page tree, \"Created by\" lines, likes, labels) and changes no other sentence. You "
          + "get: the same document, without the noise." },
      { label: "Markdown + LLM rewrite", text: "Step 2: Markdown, as above. Step 3: the same clean up, and the model "
          + "also improves the structure and the writing, keeping every fact. You get: the same facts, better written." },
      { label: "Markdown + LLM knowledge reshaping", text: "Step 2: Markdown, as above. Step 3: a model reshapes it into "
          + "a knowledge-focused document, in parts, never the whole document at once, leaving out ticket numbers, "
          + "sprints and authors. You get: one section per subject, with facts from all pages merged, for example "
          + "\"Login: SSO, with 2FA\"." },
      { label: "LLM-written document", text: "Step 2: the program splits the text into paragraphs. No markdown. Step 3: "
          + "a model writes a new document from them. You get: a summary on top, then one chapter per part of the "
          + "PDF, in the PDF's order. Hours for a large PDF." },
    ],
  },
  documentPass: {
    intro: "A model changes each finished document; nothing else runs again, and the document before stays in the "
      + "Open document menu.",
    options: [
      { label: "clean up with LLM", text: "The model takes out what a reader does not need (a wiki's page tree, "
          + "\"Created by\" lines, likes, labels) and changes no other sentence. You get: the same document, without the "
          + "noise." },
      { label: "rewrite with LLM", text: "The same clean up, and the model also improves the structure and the writing, "
          + "keeping every fact. You get: the same facts, better written." },
      { label: "knowledge reshaping with LLM", text: "The model reshapes the document into a knowledge-focused document. "
          + "It works in parts, never on the whole document at once, and leaves out ticket numbers, sprints and authors. "
          + "You get: one section per subject, with facts from all pages merged." },
    ],
  },
  documentSource: {
    intro: "What the change starts from, asked once a document was already changed.",
    options: [
      { label: "the original", text: "The document as it was before any change. The default." },
      { label: "the current document", text: "The document the last change left, so changes stack: for example a "
          + "clean up on top of the knowledge reshaping." },
    ],
  },
  pdfScans: {
    intro: "What reads a scanned page, in phase 1. A scanned page is a picture of a page with no text inside; a page "
      + "that holds real text is read as it is by pymupdf4llm (built on the PyMuPDF library), which keeps headings, "
      + "lists, tables and code.",
    options: [
      { label: "Unlimited-OCR-2", text: "A model that reads the words off such a picture; it keeps nearly every word, "
          + "about 8 to 20 seconds a page." },
      { label: "Tesseract", text: "An older word reader: about 2 seconds a page, but on a noisy scan it can lose most "
          + "of the words." },
    ],
  },
  workerSet: {
    intro: "The machine and models the run calls. A set changes only the settings it names, such as which computer's "
      + "language model answers; everything else stays as the settings have it.",
    options: [
      { label: "default", text: "The settings as they are." },
      { label: "a named set", text: "The settings with that set's changes on top, such as another machine's model server. Runs "
          + "on different sets go at the same time; runs on one set go one after another." },
    ],
  },
  settings: {
    intro: "The settings file the run reads: how many times a step may retry, which checks run, which phases are on.",
    options: [
      { label: "default settings", text: "The project's normal settings." },
      { label: "profile: fast", text: "Skips the self-checks and the review, so a run ends sooner, with less checked." },
      { label: "profile: strict", text: "No repair: the first mistake a check finds stops the run, so a person looks "
          + "at it. Used while runs are watched." },
    ],
  },
  phase: {
    intro: "The phase the run starts at (From) or the only one it runs (Only). Earlier phases are read from what the "
      + "run folder already holds.",
    options: [],
  },
  stopAfter: {
    intro: "The last phase the run goes through; the phases after it are left for later.",
    options: [{ label: "the last phase", text: "Run to the end." }],
  },
  model: {
    intro: "The language model this phase calls. A larger model writes better and takes longer.",
    options: [
      { label: "qwen3.6-35b-a3b-mtp", text: "The main model, large (35 billion parameters), reads text and pictures." },
      { label: "lfm2.5-vl-3b", text: "LiquidAI's small vision model (3 billion parameters): fast, less exact." },
      { label: "qwen3.5-4b", text: "A small model (4 billion parameters): fast, less exact." },
    ],
  },
};

// The settings profiles a run may name and the profile `i2k serve` was started with (which a run with no profile of its
// own reads), from one request. `profiles` is null until the server answers, and stays null with `error` saying why
// when it could not be read: an empty list would read as "no profile exists".
export interface SettingsProfiles {
  profiles: string[] | null;
  serverProfile: string;
  error: string;
}

export function useSettingsProfiles(): SettingsProfiles {
  const [state, setState] = useState<SettingsProfiles>({ profiles: null, serverProfile: "", error: "" });
  useEffect(() => {
    fetchJson<{ profiles: string[]; server_profile?: string }>("/profiles")
      .then((answer) => setState({ profiles: answer.profiles, serverProfile: answer.server_profile ?? "", error: "" }))
      .catch((error) => setState({ profiles: null, serverProfile: "", error: `the settings profiles could not be read: ${describeError(error)}` }));
  }, []);
  return state;
}

// Whether the settings profile a run was made with is gone: a resume naming it would be refused, so it resumes on the
// default settings instead. Unknown (not answered yet, or not readable) is not gone: the server then decides.
export function isProfileGone(madeWith: string, profiles: string[] | null): boolean {
  return Boolean(madeWith) && profiles !== null && !profiles.includes(madeWith);
}

// The ingesting profiles a run may be written in, with what each is for; a list that could not be read says why in
// `error`, so "no other profile" and "the server failed" read differently
export function useIngestingProfiles(): { profiles: IngestingProfile[]; error: string } {
  const [state, setState] = useState<{ profiles: IngestingProfile[]; error: string }>({ profiles: [], error: "" });
  useEffect(() => {
    fetchJson<{ profiles: IngestingProfile[] }>("/ingesting-profiles").then((answer) => setState({ profiles: answer.profiles, error: "" }))
      .catch((error) => setState({ profiles: [], error: `the ingesting profiles could not be read: ${describeError(error)}` }));
  }, []);
  return state;
}

// The values an untouched choice box resolves to under the chosen settings (`profile`, empty for the server's own), read
// again when the profile changes; null until the server answers or when it could not be read
export function useRunDefaults(profile: string): RunDefaults | null {
  const [defaults, setDefaults] = useState<RunDefaults | null>(null);
  useEffect(() => {
    setDefaults(null);
    fetchJson<RunDefaults>(`/run-defaults?profile=${encodeURIComponent(profile)}`).then(setDefaults).catch(() => setDefaults(null));
  }, [profile]);
  return defaults;
}

// A done PDF run's LLM clean up, rewrite or knowledge pass (phase 7): the pass chosen first,
// each row its name with its description on the right, then its box of extra wishes, loaded with the wishes that pass
// was last given (the owner: each pass keeps its own), a link to its instruction, then Queue
export function DocumentPassPanel({ profile, lastPass = "", onQueue, isDropdown = false }: {
  profile: string;
  // the pass the document last had, "" when none: only then is there a choice of what the new pass starts from
  lastPass?: string;
  onQueue: (pass: DocumentPass, wishes: string, source: DocumentSource) => void;
  // each choice a drop-down list with its help, as the run settings dialog draws its other choices (the owner: "make
  // this drop down like the other")
  isDropdown?: boolean;
}) {
  const defaults = useRunDefaults(profile);
  const [pass, setPass] = useState<"" | DocumentPass>("");
  const [wishes, setWishes] = useState("");
  const [source, setSource] = useState<DocumentSource>("original");
  const choose = (value: DocumentPass) => {
    setPass(value);
    setWishes(defaults?.document_wishes?.[value] ?? "");
  };
  const passInstruction = DOCUMENT_PASSES.find((entry) => entry.value === pass)?.instruction ?? DOCUMENT_PASSES[0].instruction;
  const lastPassName = lastPass ? describeDocumentPass(lastPass as DocumentPass) : "";
  if (isDropdown) {
    return (
      <div className="document-pass dropdowns">
        <ChoiceDropdown label="Change" help={CHOICE_HELP.documentPass} value={pass} isDefault={false}
          choices={[{ value: "", label: "choose how to change the documents", isDisabled: true },
            ...DOCUMENT_PASSES.map((entry) => ({ value: entry.value, label: `${entry.label}: ${entry.description}` }))]}
          onChange={(value) => choose(value as DocumentPass)} />
        {pass && lastPass && (
          <ChoiceDropdown label="Start from" help={CHOICE_HELP.documentSource} value={source} isDefault={source === "original"}
            choices={[{ value: "original", label: "the original: the document before any change" },
              { value: "current", label: `the current document, as the ${lastPassName} left it` }]}
            onChange={(value) => setSource(value as DocumentSource)} />
        )}
        {pass && (
          <>
            <label className="field wishes-field">
              <span>Extra wishes for the model</span>
              <textarea aria-label="extra wishes for the model" rows={3} maxLength={4000} value={wishes}
                placeholder={'what the model should also do, such as "remove every Jira link"'}
                onChange={(event) => setWishes(event.target.value)} />
            </label>
            <div className="pass-actions">
              <button type="button" className="btn ghost" title="open this pass's instruction in the Instructions editor"
                onClick={() => openInstruction(passInstruction)}>
                <Icon name="pencil" size={13} /> Edit instruction
              </button>
              <button className="btn primary" onClick={() => onQueue(pass, wishes, lastPass ? source : "original")}>
                Queue the {describeDocumentPass(pass)}
              </button>
            </div>
          </>
        )}
      </div>
    );
  }
  return (
    <div className="popover document-pass">
      <PanelNote text="Edits this run's document; the old version is kept." help={
        <p>Clean up removes an export's noise and changes no other sentence; rewrite also improves structure and wording,
          keeping every fact; knowledge writes the business facts into one section per subject, without ticket numbers,
          sprints or authors. Each version stays in the Open document menu, to open or download.</p>
      } />
      <div className="pass-list" role="radiogroup" aria-label="how to change the document">
        {DOCUMENT_PASSES.map((entry) => (
          <button key={entry.value} role="radio" aria-checked={pass === entry.value} title={entry.hint}
            className={pass === entry.value ? "on" : ""} onClick={() => choose(entry.value)}>
            <Icon name="pencil" />
            <span className="menu-text">
              <span className="pass-name">{entry.label}</span>
              <small className="pass-description">{entry.description}</small>
            </span>
          </button>
        ))}
      </div>
      {pass && (
        <>
          <label className="field wishes-field">
            <span>Extra wishes for the model</span>
            <textarea aria-label="extra wishes for the model" rows={3} maxLength={4000} value={wishes}
              placeholder={'what the model should also do, such as "remove every Jira link"'}
              onChange={(event) => setWishes(event.target.value)} />
          </label>
          <p className="muted">The wishes are kept for the next {describeDocumentPass(pass)}.</p>
          {lastPass && (
            <div className="pass-source" role="radiogroup" aria-label="start from">
              <span>Start from</span>
              {([["original", "the original: the document before any change"],
                ["current", `the current document, as the ${describeDocumentPass(lastPass as DocumentPass)} left it`]] as const)
                .map(([value, label]) => (
                  <label key={value}>
                    <input type="radio" name="pass-source" checked={source === value} onChange={() => setSource(value)} />
                    {label}
                  </label>
                ))}
            </div>
          )}
          <div className="pass-actions">
            <button type="button" className="btn ghost" title="open this pass's instruction in the Instructions editor"
              onClick={() => openInstruction(passInstruction)}>
              <Icon name="pencil" size={13} /> Edit instruction
            </button>
            <button className="btn primary" onClick={() => onQueue(pass, wishes, lastPass ? source : "original")}>
              Queue the {describeDocumentPass(pass)}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// The configured models each phase may call, from one request; with one model there is nothing to choose, and a list
// that could not be read says why in `error`
export function usePhaseModels(): { phaseModels: PhaseModels; error: string } {
  const [state, setState] = useState<{ phaseModels: PhaseModels; error: string }>({ phaseModels: { models: [], phases: [] }, error: "" });
  useEffect(() => {
    fetchJson<PhaseModels>("/phase-models").then((phaseModels) => setState({ phaseModels, error: "" }))
      .catch((error) => setState({ phaseModels: { models: [], phases: [] }, error: `the models could not be read: ${describeError(error)}` }));
  }, []);
  return state;
}

// An untouched choice names the value the settings resolve it to ("default: text only"), or plain "default" until the
// server answers: "default" alone did not say what the run would do

export function describeDefault(resolved: string | null | undefined): string {
  return resolved ? `default: ${resolved}` : "default";
}

// The Scans entry the settings' scan engine names
function describePdfScans(defaults: RunDefaults): string {
  return PDF_SCAN_ENGINES.find((entry) => entry.value === defaults.pdf_ocr_engine)?.label ?? defaults.pdf_ocr_engine;
}

// A PDF's choices: how it is processed (Processing), how its document is shaped
// (Document, whose list follows the processing: the standing shapings for Text alone), and what reads a scanned page
// (Scans). An untouched one sends no value and names what the settings
// resolve it to, by the PDF's page count when they choose by size (`pageCount`, unknown for several PDFs at once).
// Boxed for a page's toolbar, else button lists for a menu. One video's page and the inbox's ⚙ arrow share it, so
// several ticked PDFs take the same choices as one.
export function PdfChoices({ selection, onChange, defaults, pageCount, isBoxed = false, isDropdown = false }: {
  selection: PhaseSelection;
  onChange: (selection: PhaseSelection) => void;
  defaults: RunDefaults | null;
  pageCount?: number;
  isBoxed?: boolean;
  // each choice a drop-down list with a "default" pill, in the run settings dialog
  isDropdown?: boolean;
}) {
  const { profiles } = useIngestingProfiles();
  const defaultMode = choosePdfMode(defaults, pageCount);
  const defaultProcessing = readPdfProcessing(defaultMode);
  const processing = readPdfProcessing(selection.pdf_mode);
  const shaping = readPdfShaping(selection);
  // a shaping chosen with the processing kept default: the processing it stands for
  const shownProcessing = processing || (PDF_STANDING_SHAPINGS.some((entry) => entry.value === shaping) ? "text" : defaultProcessing);
  const defaultShaping = shownProcessing ? chooseDefaultShaping(shownProcessing, defaultMode) : "";
  const describeProcessing = (value: string) => PDF_PROCESSINGS.find((entry) => entry.value === value)?.label ?? "";
  const describeShaping = describePdfShaping;
  const byLimit = defaults?.pdf_mode === "by-size" ? (pageCount ? ` (${pageCount} pages)` : "by the PDF's size") : "";
  const processings = [{ value: "", label: defaultProcessing ? `default: ${describeProcessing(defaultProcessing)}${byLimit}`
    : `default: ${byLimit || "settings"}` }, ...PDF_PROCESSINGS.map((entry) => ({ value: entry.value as string, label: entry.label }))];
  const standings = shownProcessing === "pages" ? [] : PDF_STANDING_SHAPINGS;
  const shapings = [
    { value: "", label: defaultShaping ? `default: ${describeShaping(defaultShaping)}` : "default", group: "" },
    ...standings.map((entry) => ({ value: entry.value, label: entry.label, group: "" })),
    ...listPdfProfiles(profiles).map((profile) => ({ value: profile.name, label: describePdfShaping(profile.name), group: "" })),
  ];
  const scans = selection.pdf_ocr_engine ?? "";
  const scanChoices = [{ value: "", label: describeDefault(defaults && describePdfScans(defaults)) },
    ...PDF_SCAN_ENGINES.map((choice) => ({ value: choice.value as string, label: choice.label }))];
  // a choice sets the run's mode, pass and profile together; a pass chosen loads the wishes it was last given
  const choose = (nextProcessing: string, nextShaping: string) => {
    // a standing shaping kept while Pages as images is chosen means nothing there, and goes back to the default
    const keptShaping = nextProcessing === "pages" && PDF_STANDING_SHAPINGS.some((entry) => entry.value === nextShaping) ? "" : nextShaping;
    const choice = buildPdfChoice(nextProcessing as "" | PdfProcessing, keptShaping, defaultMode);
    const isSamePass = choice.document_pass && choice.document_pass === selection.document_pass;
    const wishesNow = choice.document_pass ? defaults?.document_wishes?.[choice.document_pass] ?? "" : "";
    onChange({ ...selection, ...choice, document_wishes: isSamePass ? selection.document_wishes ?? "" : wishesNow });
  };
  const setProcessing = (value: string) => choose(value, shaping);
  const setShaping = (value: string) => choose(processing, value);
  const wishes = selection.document_pass ? (
    <label className="field wishes-field">
      <span>Extra wishes for the model</span>
      <textarea aria-label="extra wishes for the model" rows={3} maxLength={4000}
        placeholder={'what the model should also do, such as "remove every Jira link"'}
        value={selection.document_wishes ?? ""}
        onChange={(event) => onChange({ ...selection, document_wishes: event.target.value })} />
    </label>
  ) : null;
  const setScans = (value: string) => {
    const choice = PDF_SCAN_ENGINES.find((entry) => entry.value === value);
    onChange({ ...selection, pdf_ocr_engine: choice?.value ?? "" });
  };
  const processingTitle = `Processing: ${PDF_PROCESSINGS.map((entry) => `${entry.label}: ${entry.hint}`).join("; ")}`;
  const shapingTitle = `Document: ${PDF_STANDING_SHAPINGS.map((entry) => `${entry.label}: ${entry.hint}`).join("; ")}; `
    + "a profile: a model writes the document in that shape";
  // a list's options, those of a shaping group under its heading
  const renderOptions = (choices: { value: string; label: string; group?: string }[]) => {
    const groups = [...new Set(choices.map((choice) => choice.group ?? ""))];
    return groups.map((group) => {
      const options = choices.filter((choice) => (choice.group ?? "") === group)
        .map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>);
      return group ? <optgroup key={group} label={group}>{options}</optgroup> : options;
    });
  };
  if (isDropdown) {
    // the default shown as the value it resolves to, with the pill; picking that value keeps the default (""), so a
    // change of the settings' default still reaches the run
    const defaultScans = defaults ? PDF_SCAN_ENGINES.find((choice) => choice.value === defaults.pdf_ocr_engine)?.value ?? "" : "";
    const withDefault = (choices: { value: string; label: string }[], resolved: string) =>
      resolved ? choices.slice(1) : choices;
    return (
      <>
        <ChoiceDropdown label="Processing" title={processingTitle} help={CHOICE_HELP.pdfProcessing} choices={processings}
          value={processing} isDefault={!processing} onChange={setProcessing} />
        <ChoiceDropdown label="Document" title={shapingTitle} help={CHOICE_HELP.pdfShaping} choices={shapings}
          value={shaping} isDefault={!shaping} onChange={setShaping} />
        {wishes}
        <ChoiceDropdown label="Scans" help={CHOICE_HELP.pdfScans} choices={withDefault(scanChoices, defaultScans)}
          value={scans || defaultScans} isDefault={!scans}
          onChange={(value) => setScans(value === defaultScans ? "" : value)} />
      </>
    );
  }
  if (isBoxed) {
    return (
      <>
        <label className="field-select" title={processingTitle}>
          <small>Processing</small>
          <select aria-label="processing" value={processing} onChange={(event) => setProcessing(event.target.value)}>
            {renderOptions(processings)}
          </select>
        </label>
        <label className="field-select" title={shapingTitle}>
          <small>Document</small>
          <select aria-label="document shaping" value={shaping} onChange={(event) => setShaping(event.target.value)}>
            {renderOptions(shapings)}
          </select>
        </label>
        <label className="field-select">
          <small>Scans</small>
          <select aria-label="scans" value={scans} onChange={(event) => setScans(event.target.value)}>
            {scanChoices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
          </select>
        </label>
        {/* last, on a row of its own: between the lists it pulled them onto rows of different heights */}
        {wishes && <div className="wishes-row">{wishes}</div>}
      </>
    );
  }
  const buttonList = (name: string, choices: { value: string; label: string }[], value: string, set: (value: string) => void) => (
    <div className="phase-list" role="radiogroup" aria-label={name}>
      {choices.map((choice) => (
        <button key={choice.value} role="radio" aria-checked={value === choice.value} className={value === choice.value ? "on" : ""}
          onClick={() => set(choice.value)}>{choice.label}</button>
      ))}
    </div>
  );
  return (
    <>
      <div className="field" title={processingTitle}><span>Processing</span>{buttonList("processing", processings, processing, setProcessing)}</div>
      <div className="field" title={shapingTitle}><span>Document</span>{buttonList("document shaping", shapings, shaping, setShaping)}</div>
      {wishes}
      <div className="field"><span>Scans</span>{buttonList("scans", scanChoices, scans, setScans)}</div>
    </>
  );
}

// One row per phase that calls a model: the phase, then the model it calls in this run
export function ModelRows({ phaseModels, selection, onChange, workflow, isDropdown = false }: {
  phaseModels: PhaseModels;
  selection: PhaseSelection;
  onChange: (selection: PhaseSelection) => void;
  // each phase's model a drop-down list with a "default" pill, in the run settings dialog
  isDropdown?: boolean;
  // the workflow the runs take and its phases as it titles them: only those that call a model in it are listed, under
  // its name (the owner: the models did not say which workflow they were for, and listed phases it never runs)
  workflow?: { name: string; phases: Phase[] };
}) {
  const setPhaseModel = (phase: string, name: string) => {
    const chosen = { ...(selection.phase_models ?? {}) };
    if (name) chosen[phase] = name;
    else delete chosen[phase];
    onChange({ ...selection, phase_models: chosen });
  };
  const flowPhaseOf = (number: number) => workflow?.phases.find((phase) => phase.number === number);
  const rows = phaseModels.phases.filter((entry) => {
    const phase = flowPhaseOf(entry.number);
    return !workflow || (phase !== undefined && phase.is_on !== false && phase.is_model_used !== false);
  });
  return (
    <div className="phase-models">
      {workflow && !rows.length && <p className="muted">No phase of this workflow calls a model.</p>}
      {isDropdown && rows.map((entry) => (
        <ChoiceDropdown key={entry.phase} label={`${entry.number} · ${flowPhaseOf(entry.number)?.title || entry.title}`}
          help={CHOICE_HELP.model}
          value={selection.phase_models?.[entry.phase] ?? ""} isDefault={!selection.phase_models?.[entry.phase]}
          onChange={(value) => setPhaseModel(entry.phase, value)}
          // the default named by its model alone: the pill says it is the default (the owner)
          choices={listModelChoices(phaseModels, entry.default).map((choice) => ({ ...choice,
            label: choice.label.replace(/ \(default\)$/, "") }))} />
      ))}
      {!isDropdown && rows.map((entry) => (
        <div key={entry.phase} className="model-row">
          <span>{entry.number} · {flowPhaseOf(entry.number)?.title || entry.title}</span>
          {/* buttons, not a select: a browser's own list for a select in a menu opened far from its box */}
          <div className="model-choices" role="radiogroup" aria-label={`model of ${entry.title}`}>
            {listModelChoices(phaseModels, entry.default).map((choice) => {
              const isOn = (selection.phase_models?.[entry.phase] ?? "") === choice.value;
              return (
                <button key={choice.value} role="radio" aria-checked={isOn} className={`chip small${isOn ? " on" : ""}`}
                  onClick={() => setPhaseModel(entry.phase, choice.value)}>{choice.label}</button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}

// The Models button's words: "default models" until a phase is given another model, then how many were
export function describeModels(selection: PhaseSelection): string {
  const count = Object.keys(selection.phase_models ?? {}).length;
  return count ? `models: ${count} changed` : "default models";
}

// The models' choice where a "Models" label stands before it: "default", or how many phases have another model
export function describeModelChoice(selection: PhaseSelection): string {
  const count = Object.keys(selection.phase_models ?? {}).length;
  return count ? `${count} changed` : "default";
}
