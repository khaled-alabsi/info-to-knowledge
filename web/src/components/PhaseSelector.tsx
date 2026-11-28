import { useState } from "react";
import type { ReactNode } from "react";
import type { Phase, PhaseModels, PhaseSelection } from "../api";
import { formatPhaseTitle } from "../api";
import { CHOICE_HELP, ChoiceDropdown, ModelRows, describeModels } from "./RunChoices";
import Icon from "./Icon";
import { ProviderSetChoice, useProviderSets } from "./ProviderSetChoice";
import { TagInput, mergeTags } from "./TagInput";

type Mode = "all" | "from" | "only";

const MODE_LABELS: Record<Mode, string> = { all: "Every phase", from: "From", only: "Only" };

function readMode(selection: PhaseSelection): Mode {
  if (selection.only_phase !== null) return "only";
  if (selection.from_phase !== null) return "from";
  return "all";
}

// What a run is started with beside its phases, in one vocabulary for every place that says it: named in full where
// the line has room (the Phases button: "tags: chat, demo · phase 3: lfm-vl-3b"), counted where it is short (the
// "Run with" line: "tags: 2, models: 1 changed", the defaults named). A phase's number is the server's.
function listRunWith(selection: PhaseSelection, phaseModels: PhaseModels, form: "named" | "counted", isTagsShown = true): string[] {
  const tags = selection.tags ?? [];
  if (form === "counted") {
    const parts = [selection.profile ? `profile: ${selection.profile}` : "default settings", describeModels(selection)];
    if (selection.provider_set) parts.push(`provider set: ${selection.provider_set}`);
    if (selection.is_dry_run) parts.unshift("dry run");
    if (isTagsShown && tags.length) parts.push(`tags: ${tags.length}`);
    return parts;
  }
  const numberOf = (phase: string) => phaseModels.phases.find((entry) => entry.phase === phase)?.number ?? phase;
  return [
    ...(selection.profile ? [`profile: ${selection.profile}`] : []),
    ...(tags.length ? [`tags: ${tags.join(", ")}`] : []),
    ...Object.entries(selection.phase_models ?? {}).map(([phase, name]) => `phase ${numberOf(phase)}: ${name}`),
    ...(selection.is_dry_run ? ["dry run"] : []),
  ];
}

// Which phases a run covers, and a dry run: the Phases button's words where the page shows the other choices itself
// A folded workflow's title line: "default models", or how many of its phases have a model chosen
function describeChosenModels(phases: Phase[], selection: PhaseSelection): string {
  const chosenCount = phases.filter((phase) => selection.phase_models?.[phase.name]).length;
  return chosenCount ? `${chosenCount} model${chosenCount === 1 ? "" : "s"} chosen` : "default models";
}

export function describePhases(selection: PhaseSelection): string {
  const parts = [];
  if (selection.only_phase !== null) parts.push(`phase ${selection.only_phase}`);
  else if (selection.from_phase !== null) parts.push(`from ${selection.from_phase}`);
  else if (selection.to_phase !== null) parts.push(`phases 1 to ${selection.to_phase}`);
  else parts.push("every phase");
  if (selection.to_phase !== null && selection.from_phase !== null && selection.only_phase === null) parts.push(`to ${selection.to_phase}`);
  if (selection.is_dry_run) parts.push("dry run");
  return parts.join(" · ");
}

// The Phases button's words: which phases, then what else differs from the defaults
export function describeSelection(selection: PhaseSelection, phaseModels: PhaseModels): string {
  return [describePhases({ ...selection, is_dry_run: false }), ...listRunWith(selection, phaseModels, "named")].join(" · ");
}

// Which phases a run covers: every phase, phase N onward, or phase N alone; optionally up to a last phase; and whether it
// is a dry run (prompts written, no model called). Then, under "Run with", the settings and each phase's model, and the
// tags, unless the page shows those itself.
export default function PhaseSelector({
  phases,
  phaseModels,
  selection,
  onChange,
  isTagsShown = true,
  isRunWithShown = true,
  isProviderSetShown = true,
  layout = "folded",
  children,
  extraTabs = [],
  workflows = [],
  settingsExtra,
  settingsExtraSummary = "",
}: {
  phases: Phase[];
  phaseModels: PhaseModels;
  selection: PhaseSelection;
  onChange: (selection: PhaseSelection) => void;
  // false where the page shows the tags itself (a new video's page, under its title)
  isTagsShown?: boolean;
  // false where the page shows the settings and the models itself (a new video's page, in its toolbar)
  isRunWithShown?: boolean;
  // false where the page shows the Provider set in its own row of choices (a new recording's page)
  isProviderSetShown?: boolean;
  // "folded" in a narrow menu, each part under a line that opens it; "tabs" in a dialog, a tab each part
  layout?: "folded" | "tabs";
  // further parts the page adds after these in the folded layout
  children?: ReactNode;
  // further tabs the page adds after these in the tabs layout (the ticked PDFs' choices)
  extraTabs?: { key: string; label: string; summary: string; body: ReactNode }[];
  // each workflow the runs take ("PDF workflow · text only") with its phases: the models tab shows a section each
  workflows?: { name: string; phases: Phase[] }[];
  // a choice the page adds on top of the Settings tab, and its words in that tab's summary (a video run's Workflow
  // speed: one dropdown, no tab of its own, the owner)
  settingsExtra?: ReactNode;
  settingsExtraSummary?: string;
}) {
  const [tab, setTab] = useState("phases");
  const isDropdown = layout === "tabs";
  // each phase of this workflow with what it does, for the phase lists' help
  const phaseHelp = phases.map((phase) => ({ label: `${phase.number} · ${formatPhaseTitle(phase)}`,
    text: phase.summary ?? "" }));
  const mode = readMode(selection);
  const start = selection.only_phase ?? selection.from_phase ?? phases[0]?.number ?? 1;

  const setMode = (next: Mode) =>
    onChange({
      ...selection,
      only_phase: next === "only" ? start : null,
      from_phase: next === "from" ? start : null,
      to_phase: next === "only" ? null : selection.to_phase,
    });
  // a stop before the new start is cleared: it would stop before anything ran
  const setStart = (number: number) =>
    onChange({ ...selection, only_phase: mode === "only" ? number : null, from_phase: mode === "from" ? number : null,
      to_phase: selection.to_phase !== null && selection.to_phase < number ? null : selection.to_phase });

  const changedModels = Object.keys(selection.phase_models ?? {}).length;
  const isAnyChanged = selection.is_dry_run || Boolean(selection.profile) || changedModels > 0 || (selection.tags ?? []).length > 0;
  // "Run with default settings, default models", or what was changed: "Run with dry run, profile: strict, tags: 2"
  const describeMore = () => `Run with ${listRunWith(selection, phaseModels, "counted", isTagsShown).join(", ")}`;
  // An accordion: opening "Run with" folds the phases and the other way round, to save height (the owner)
  const [openPart, setOpenPart] = useState<"phases" | "more" | null>(isAnyChanged ? "more" : "phases");
  const toggle = (part: "phases" | "more") => (event: { preventDefault: () => void }) => {
    event.preventDefault();
    setOpenPart(openPart === part ? null : part);
  };

  const phasesBody = (
    <>
        <div className="segmented" role="radiogroup" aria-label="which phases">
          {(["all", "from", "only"] as Mode[]).map((option) => (
            <button key={option} role="radio" aria-checked={mode === option} className={mode === option ? "on" : ""}
              onClick={() => setMode(option)}>
              {MODE_LABELS[option]}
            </button>
          ))}
        </div>
        {/* the phases as buttons the page draws itself: a browser's own list for a select inside this panel opened far
            from its box (the owner's screenshot) */}
        {/* in the dialog, drop-down lists, the default marked by a pill (the owner) */}
        {isDropdown && mode !== "all" && (
          <ChoiceDropdown label="Phase" value={String(start)} isDefault={false} onChange={(value) => setStart(Number(value))}
            help={{ ...CHOICE_HELP.phase, options: phaseHelp }}
            choices={phases.map((phase) => ({ value: String(phase.number), isDisabled: phase.is_on === false,
              label: `${phase.number} · ${formatPhaseTitle(phase)}${phase.is_on === false ? " (off)" : ""}` }))} />
        )}
        {isDropdown && mode !== "only" && (
          <ChoiceDropdown label="Stop after" value={String(selection.to_phase ?? "")} isDefault={selection.to_phase == null}
            help={{ ...CHOICE_HELP.stopAfter, options: [...CHOICE_HELP.stopAfter.options, ...phaseHelp] }}
            onChange={(value) => onChange({ ...selection, to_phase: value ? Number(value) : null })}
            choices={[{ value: "", label: "the last phase" },
              ...phases.filter((phase) => phase.number >= start && phase.is_on !== false)
                .map((phase) => ({ value: String(phase.number), label: `${phase.number} · ${formatPhaseTitle(phase)}` }))]} />
        )}
        {!isDropdown && mode !== "all" && (
          <div className="field">
            <span>Phase</span>
            <div className="phase-list" role="radiogroup" aria-label="phase">
              {phases.map((phase) => (
                <button key={phase.number} role="radio" aria-checked={start === phase.number} disabled={phase.is_on === false}
                  className={start === phase.number ? "on" : ""} onClick={() => setStart(phase.number)}>
                  {phase.number} · {formatPhaseTitle(phase)}{phase.is_on === false ? " (off)" : ""}
                </button>
              ))}
            </div>
          </div>
        )}
        {!isDropdown && mode !== "only" && (
          <div className="field">
            <span>Stop after</span>
            <div className="phase-list" role="radiogroup" aria-label="stop after">
              {/* a phase the flow never runs (a PDF read as text only has no review) is no place to stop */}
              {[null, ...phases.filter((phase) => phase.number >= start && phase.is_on !== false).map((phase) => phase.number)].map((number) => {
                const phase = phases.find((candidate) => candidate.number === number);
                const isOn = (selection.to_phase ?? null) === number;
                return (
                  <button key={number ?? "last"} role="radio" aria-checked={isOn} className={isOn ? "on" : ""}
                    onClick={() => onChange({ ...selection, to_phase: number })}>
                    {phase ? `${phase.number} · ${formatPhaseTitle(phase)}` : "the last phase"}
                  </button>
                );
              })}
            </div>
          </div>
        )}
    </>
  );
  const dryRunSwitch = (
      <label className="switch">
        <input type="checkbox" checked={selection.is_dry_run}
          onChange={(event) => onChange({ ...selection, is_dry_run: event.target.checked })} />
        <span className="track" />
        <span>Dry run <small>prompts only, no model call</small></span>
      </label>
  );
  const providerSets = useProviderSets();
  const providerSetField = isRunWithShown && isProviderSetShown
    ? <ProviderSetChoice sets={providerSets} selection={selection} onChange={onChange} isDropdown={isDropdown} /> : null;
  const modelRows = isRunWithShown && phaseModels.models.length > 1
    ? (isDropdown && workflows.length
      ? <>{workflows.map((workflow) => (
          // each workflow folds, closed at first, its title saying whether any model is chosen in it (the owner:
          // several workflows' rows made one long scroll)
          <section key={workflow.name} className="workflow-models" aria-label={workflow.name}>
            <details className="settings-section">
              <summary><span className="caret"><Icon name="chevron" size={13} /></span>
                <span className="workflow-heading">{workflow.name}</span>
                <small className="muted">{describeChosenModels(workflow.phases, selection)}</small></summary>
              <ModelRows phaseModels={phaseModels} selection={selection} onChange={onChange} isDropdown workflow={workflow} />
            </details>
          </section>))}</>
      : <ModelRows phaseModels={phaseModels} selection={selection} onChange={onChange} isDropdown={isDropdown} />)
    : null;
  const tagsField = isTagsShown && (
      <div className="field">
        <span>Tags</span>
        <div className="run-tags">
          {(selection.tags ?? []).map((tag) => (
            <span key={tag} className="pill tag">
              {tag}
              <button className="tag-remove" aria-label={`remove tag ${tag}`}
                onClick={() => onChange({ ...selection, tags: selection.tags.filter((other) => other !== tag) })}>×</button>
            </span>
          ))}
          <TagInput given={selection.tags ?? []} onAdd={(added) => onChange({ ...selection, tags: mergeTags(selection.tags ?? [], added) })} />
        </div>
        <small className="muted">a tag naming an ingesting profile ("chat", "learn German") picks it</small>
      </div>
  );

  // In a dialog each part is a tab of its own, its choice named under its title: as cards side by side they made a
  // ragged wall of widgets (the owner: "make tab in it and each tab has its own config")
  if (layout === "tabs") {
    const tabs = [
      { key: "phases", label: "Phases", summary: describePhases({ ...selection, is_dry_run: false }), body: phasesBody },
      { key: "settings", label: "Settings", summary: [settingsExtraSummary,
          ...listRunWith(selection, phaseModels, "counted", isTagsShown).filter((part) => part !== describeModels(selection))]
          .filter(Boolean).join(", "),
        body: <>{settingsExtra}{providerSetField}{dryRunSwitch}{tagsField}</> },
      ...(modelRows ? [{ key: "models", label: "Models", summary: describeModels(selection), body: modelRows }] : []),
      ...extraTabs,
    ];
    const shown = tabs.find((entry) => entry.key === tab) ?? tabs[0];
    return (
      <div className="run-settings-tabs">
        <div className="settings-tab-list" role="tablist" aria-label="run settings">
          {tabs.map((entry) => (
            <button key={entry.key} role="tab" aria-selected={entry.key === shown.key}
              className={entry.key === shown.key ? "on" : ""} onClick={() => setTab(entry.key)}>
              <span>{entry.label}</span>
              <small className="muted">{entry.summary}</small>
            </button>
          ))}
        </div>
        <section className="settings-tab-panel" role="tabpanel" aria-label={shown.label.toLowerCase()}>
          {shown.body}
        </section>
      </div>
    );
  }

  return (
    <div className="phase-form">
      <details className="phase-more phase-part" open={openPart === "phases"}>
        <summary onClick={toggle("phases")}><span className="caret"><Icon name="chevron" size={13} /></span>
          Phases: {describePhases({ ...selection, is_dry_run: false })}</summary>
        {phasesBody}
      </details>
      {/* the choices most runs leave as they are, folded under one line that says what they hold; open by itself when
          any differs from the default */}
      <details className="phase-more" open={openPart === "more"}>
        <summary onClick={toggle("more")}><span className="caret"><Icon name="chevron" size={13} /></span>{describeMore()}</summary>
        {dryRunSwitch}
        {(providerSetField || modelRows) && (
          <>
            <div className="menu-heading">Run with</div>
            {providerSetField}
            {modelRows && <div className="field"><span>Models</span>{modelRows}</div>}
          </>
        )}
        {tagsField}
      </details>
      {children}
    </div>
  );
}
