import { useEffect, useState } from "react";
import { describeError, fetchJson, saveSetting } from "../api";
import type { MissingProvider, SettingRow } from "../api";
import { describeRunValue, isOwnerChange } from "../settingRunValue";
import { usePersistentState } from "../viewState";
import Icon from "./Icon";
import OptionMenu from "./OptionMenu";
import { useSettingsProfiles } from "./RunChoices";
import Spinner from "./Spinner";
import WorkerSetsPanel, { WORKER_SET_KEYS } from "./WorkerSetsPanel";

// A change typed but not saved yet: its row, and the editor's value as it stands (text, or a checkbox's state)
export type SettingDraft = { row: SettingRow; raw: string | boolean };
export type SettingDrafts = Record<string, SettingDraft>;

// The value an editor holds for a row: a checkbox's state, or text (JSON for lists and maps)
function readEditorValue(row: SettingRow): string | boolean {
  if (row.kind === "bool") return Boolean(row.value);
  // laid out one field a line: written on one line, a list of rules ran past its box (the owner)
  if (row.kind === "json") return JSON.stringify(row.value, null, 2);
  return row.value === null || row.value === undefined ? "" : String(row.value);
}

function parseValue(row: SettingRow, raw: string | boolean): unknown {
  if (row.kind === "bool") return raw;
  const text = String(raw);
  if (row.kind === "number") return text.trim() === "" ? null : Number(text);
  if (row.kind === "json") return JSON.parse(text);
  return text === "" && row.value === null ? null : text;
}

// Every draft saved, one setting at a time, in both default settings files or in the profile's file; the ones refused stay drafts.
// Returns the drafts left and one line for the page's message.
export async function saveDrafts(drafts: SettingDrafts, profile: string): Promise<{ left: SettingDrafts; message: string; isError: boolean }> {
  const left: SettingDrafts = {};
  const refusals: string[] = [];
  let saved = 0;
  for (const [key, draft] of Object.entries(drafts)) {
    // a value typed as JSON that does not parse is the person's to fix; it is not sent
    let value: unknown;
    try {
      value = parseValue(draft.row, draft.raw);
    } catch (error) {
      if (!(error instanceof SyntaxError)) throw error;
      left[key] = draft;
      refusals.push(`${key}: not valid JSON`);
      continue;
    }
    try {
      const answer = await saveSetting(key, value, profile, draft.row.path);
      if (answer.exit_code === 0) saved += 1;
      else {
        left[key] = draft;
        refusals.push(answer.output);
      }
    } catch (error) {
      left[key] = draft;
      refusals.push(`${key}: ${describeError(error)}`);
    }
  }
  const done = saved ? `${saved} setting${saved > 1 ? "s" : ""} saved; the next run reads them` : "";
  return { left, message: [done, ...refusals].filter(Boolean).join(" · "), isError: refusals.length > 0 };
}

function isJson(text: string): boolean {
  try {
    JSON.parse(text);
    return true;
  } catch {
    // only a parse error is expected here: the text is what the person is typing
    return false;
  }
}

// A setting with no dot in its key stands in the "general" section, never in a section of its own
const GENERAL = "general";

// The worker sets' keys have no dot but are a subject of their own (the owner): they stand in their own section, first
const WORKER_SETS = "workerSets";

function readSectionName(row: SettingRow): string {
  if (WORKER_SET_KEYS.has(row.key)) return WORKER_SETS;
  return row.key.includes(".") ? row.key.split(".")[0] : GENERAL;
}

// The sections in the order the rows give them, the worker sets' first
function orderSections(names: string[]): string[] {
  return names.includes(WORKER_SETS) ? [WORKER_SETS, ...names.filter((name) => name !== WORKER_SETS)] : names;
}

// A section whose key is a name, written as the name is written
const SECTION_TITLES: Record<string, string> = { pdf: "PDF", [WORKER_SETS]: "Worker sets" };

// "framesProcessing" as "Frames processing"
function renderSectionTitle(section: string): string {
  if (SECTION_TITLES[section]) return SECTION_TITLES[section];
  const words = section.replace(/([A-Z])/g, " $1").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

// The section whose entries are models, each under a name the person gives
const MODELS_SECTION = "models";

// The configured models' names, read from the rows of their fields (`models.<name>.<field>`)
function listModelNames(rows: SettingRow[]): string[] {
  return [...new Set(rows.filter((row) => row.path?.[0] === MODELS_SECTION && row.path.length > 2).map((row) => row.path![1]))];
}

// A new model's settings, copied from `source`: every field a settings file sets for it, so the copy reads as it does
function copyModelSettings(rows: SettingRow[], source: string): Record<string, unknown> {
  const fields = rows.filter((row) => row.path?.[0] === MODELS_SECTION && row.path[1] === source && row.path.length === 3);
  const setFields = fields.filter((row) => row.source !== "code");
  return Object.fromEntries((setFields.length ? setFields : fields.filter((row) => row.path![2] === "spec"))
    .map((row) => [row.path![2], row.value]));
}

// The row a new model stands on until it is saved: its whole entry, edited as JSON
function buildNewModelRow(name: string, source: string): SettingRow {
  return { key: `${MODELS_SECTION}.${name}`, path: [MODELS_SECTION, name], value: null, kind: "json", source: "new",
    comment: `A new model, copied from ${source}. Save settings writes it; then each of its fields can be chosen from its list.` };
}

// A comment's `code` spans drawn as code, the rest as plain text
function renderComment(comment: string) {
  return comment.split(/(`[^`]+`)/).map((part, index) =>
    part.startsWith("`") && part.endsWith("`") && part.length > 2 ? <code key={index}>{part.slice(1, -1)}</code> : part);
}

// Every setting with its value in force, the file that set it and its comment. A change is a draft, marked, until the
// top bar's Save settings writes them all; the next run reads them.
export default function SettingsView({ profile, onProfileChange, drafts, onDraft, version, onMessage, onOpenSetup,
  isSetupNeeded = false, missingProviders = [] }: {
  profile: string;
  onProfileChange: (profile: string) => void;
  drafts: SettingDrafts;
  onDraft: (key: string, draft: SettingDraft | null) => void;
  version: number;
  onMessage: (text: string, isError?: boolean) => void;
  // opens the setup wizard: the model host, the models and the tools, also on a machine where nothing is missing
  onOpenSetup?: () => void;
  // something a run needs is missing: the wizard's button says so, as the gear's mark did
  isSetupNeeded?: boolean;
  // in a container there is no wizard: the models a run calls that have no reachable provider, named above the settings
  missingProviders?: MissingProvider[];
}) {
  const settingsProfiles = useSettingsProfiles();
  const profiles = settingsProfiles.profiles ?? [];
  const [rows, setRows] = useState<SettingRow[] | null>(null);
  const [filter, setFilter] = useState("");
  // Every group closed until opened, and kept open when the settings are left and opened again; a search opens each
  // group holding a match
  const [openSections, setOpenSections] = usePersistentState<string[]>("settingsSections", []);
  // The rows whose value is being typed in a box, after "Other…" was picked from their list
  const [typedKeys, setTypedKeys] = useState<string[]>([]);
  // The "+ Add model" form while it is open: the new model's name and the model it is copied from
  const [newModel, setNewModel] = useState<{ name: string; source: string } | null>(null);
  // The file chosen in the Edit menu while changes were unsaved, waiting for the person to keep or drop them
  const [pendingProfile, setPendingProfile] = useState<string | null>(null);
  const draftCount = Object.keys(drafts).length;
  const describeFile = (name: string) => name ? `profile ${name}` : "the default settings";
  // a draft belongs to the file it was typed for: the switch drops them all (App's onProfileChange)
  const switchProfile = () => {
    if (pendingProfile === null) return;
    onProfileChange(pendingProfile);
    setPendingProfile(null);
  };

  useEffect(() => {
    if (settingsProfiles.error) onMessage(settingsProfiles.error, true);
  }, [settingsProfiles.error, onMessage]);
  // another file's rows are never shown under this file's name while its own load
  useEffect(() => setRows(null), [profile]);
  useEffect(() => {
    fetchJson<SettingRow[]>(`/settings${profile ? `?profile=${encodeURIComponent(profile)}` : ""}`)
      .then(setRows).catch((error: Error) => onMessage(error.message, true));
  }, [profile, version, onMessage]);

  const edit = (row: SettingRow, raw: string | boolean) => onDraft(row.key, raw === readEditorValue(row) ? null : { row, raw });
  const needle = filter.trim().toLowerCase();
  // a model added and not saved yet stands among the rows, as a draft of its own
  const rowKeys = new Set((rows ?? []).map((row) => row.key));
  const newRows = Object.values(drafts).map((draft) => draft.row).filter((row) => !rowKeys.has(row.key));
  const shown = [...(rows ?? []), ...newRows].filter((row) => !needle || row.key.toLowerCase().includes(needle)
    || row.comment.toLowerCase().includes(needle));
  const modelNames = listModelNames(rows ?? []);
  const takenNames = new Set([...modelNames, ...newRows.map((row) => row.path?.[1] ?? "")]);
  const newModelName = newModel?.name.trim() ?? "";
  const addModel = () => {
    if (!newModel || !newModelName || takenNames.has(newModelName)) return;
    onDraft(`${MODELS_SECTION}.${newModelName}`, { row: buildNewModelRow(newModelName, newModel.source),
      raw: JSON.stringify(copyModelSettings(rows ?? [], newModel.source), null, 2) });
    setNewModel(null);
  };
  // A model spec's sibling row naming the model's host (`models.<name>.host`), and the host in force: its draft when one
  // is chosen (empty meaning the host named like the provider), else the one the server says the model runs on
  const findHostRow = (specRow: SettingRow) => (rows ?? []).find((candidate) => candidate.path?.length === 3
    && candidate.path[0] === specRow.path?.[0] && candidate.path[1] === specRow.path?.[1] && candidate.path[2] === "host");
  const readModelHost = (specRow: SettingRow): string | undefined => {
    if (specRow.chosen_host === undefined) return undefined;
    const hostRow = findHostRow(specRow);
    const draft = hostRow ? drafts[hostRow.key] : undefined;
    if (!draft) return specRow.chosen_host;
    return String(draft.raw) || specRow.default_host;
  };
  const setTyped = (key: string, isTyped: boolean) =>
    setTypedKeys((current) => isTyped ? [...new Set([...current, key])] : current.filter((name) => name !== key));
  const sections = orderSections([...new Set(shown.map(readSectionName))]);

  return (
    <section className="settings-view">
      <header className="settings-head">
        <h2>Settings</h2>
        {onOpenSetup && (isSetupNeeded
          ? <button className="btn setup-button" title="Something a run needs is not set up" onClick={onOpenSetup}>
              <Icon name="alert" /> Setup needed</button>
          : <button className="btn" onClick={onOpenSetup}><Icon name="settings" /> Setup wizard</button>)}
        {/* one row of controls of one height, each label inside its control (the owner: the labels above them put the
            boxes out of line with the title and the button) */}
        {/* the default settings alone: the server's own profile is listed by its name like any other, never folded into
            this entry, whose values hold no profile */}
        <OptionMenu name="Edit" prefix="Edit" value={profile} options={[
          { value: "", label: "default settings", hint: "both default settings files", group: "" },
          ...profiles.map((name) => ({ value: name, label: `profile: ${name}`, group: "",
            hint: name === settingsProfiles.serverProfile ? "the server runs on it" : "" }))]}
          onChoose={(next) => {
            if (next === profile) return;
            // unsaved changes are lost on a switch: ask first, and switch at once when there are none (the owner)
            if (draftCount > 0) setPendingProfile(next); else onProfileChange(next);
          }} />
        <input type="search" className="grow" aria-label="Find" value={filter}
          placeholder="Find a key or a word of its comment" onChange={(event) => setFilter(event.target.value)} />
      </header>
      <p className="muted">A change is marked until you press Save settings in the top bar. A run already going keeps its values;
        the next run reads the files at its start.</p>
      {missingProviders.length > 0 && (
        <div className="settings-switch-warning" role="alert">
          <p><strong>A model provider a run needs is not set up.</strong> A run is refused until each model below has one:
            choose its host and model here, then press Save settings.</p>
          <ul className="settings-missing-providers">
            {missingProviders.map((provider) => (
              <li key={provider.name}><strong>{provider.name}</strong> (called by {provider.used_by.join(", ")}): {provider.reason}</li>
            ))}
          </ul>
        </div>
      )}
      {pendingProfile !== null && (
        <div className="settings-switch-warning" role="alert">
          <p>You have {draftCount === 1 ? "1 unsaved change" : `${draftCount} unsaved changes`} to {describeFile(profile)}.
            Switching to {describeFile(pendingProfile)} discards them; press Save settings first to keep them.</p>
          <div className="settings-switch-actions">
            <button className="btn" onClick={() => setPendingProfile(null)}>Stay on {describeFile(profile)}</button>
            <button className="btn primary" onClick={switchProfile}>Discard the changes and switch</button>
          </div>
        </div>
      )}
      {/* a profile sets only what differs, so the page names what it sets: choosing one changes those rows alone (the
          owner, choosing profile mbp and seeing the same first rows: "the configs dont change") */}
      {rows !== null && profile && (() => {
        const profileRows = rows.filter((row) => row.source === profile);
        return (
          <p className="settings-profile-note">
            {profileRows.length === 0 ? <>Profile {profile} sets no setting: every value is the default settings'.</> : <>
              Profile {profile} sets {profileRows.length === 1 ? "1 setting" : `${profileRows.length} settings`}:{" "}
              {profileRows.map((row, index) => (
                <span key={row.key}>{index > 0 && ", "}
                  <button className="link-button" onClick={() => setFilter(row.key)}>{row.key}</button>
                </span>
              ))}. Every other value is the default settings'.</>}
          </p>
        );
      })()}
      {rows === null && <Spinner />}
      {rows !== null && needle && shown.length === 0 && (
        <p className="muted">No setting's key or comment holds "{filter.trim()}".</p>
      )}
      {sections.map((section) => {
        const sectionRows = shown.filter((row) => readSectionName(row) === section);
        const edited = sectionRows.filter((row) => drafts[row.key]).length;
        return (
          <details key={section} className="settings-section" open={Boolean(needle) || openSections.includes(section)}
            onToggle={(event) => {
              if (needle) return;
              const isOpen = event.currentTarget.open;
              setOpenSections((current) => isOpen ? [...new Set([...current, section])] : current.filter((name) => name !== section));
            }}>
            <summary><span className="caret"><Icon name="chevron" size={13} /></span><h3>{renderSectionTitle(section)}</h3><span className="muted">{sectionRows.length}{edited ? ` · ${edited} changed` : ""}</span></summary>
            {section === MODELS_SECTION && (newModel === null ? (
              <div className="setting-row add-model">
                <button className="btn" onClick={() => setNewModel({ name: "", source: modelNames[0] ?? "" })}
                  disabled={modelNames.length === 0}>+ Add model</button>
              </div>
            ) : (
              <div className="setting-row edited add-model">
                <input type="text" aria-label="New model's name" placeholder="name, as frame-other-mac" autoFocus
                  value={newModel.name} onChange={(event) => setNewModel({ ...newModel, name: event.target.value })}
                  onKeyDown={(event) => { if (event.key === "Enter") addModel(); }} />
                <OptionMenu name="Copy of" prefix="Copy of" value={newModel.source}
                  onChoose={(source) => setNewModel({ ...newModel, source })}
                  options={modelNames.map((name) => ({ value: name, label: name, hint: "", group: "" }))} />
                <button className="btn primary" onClick={addModel}
                  disabled={!newModelName || takenNames.has(newModelName)}>Add</button>
                <button className="btn" onClick={() => setNewModel(null)}>Cancel</button>
                {takenNames.has(newModelName) && <small className="error">a model has this name</small>}
              </div>
            ))}
            {section === WORKER_SETS && <WorkerSetsPanel rows={rows ?? []} drafts={drafts} onEdit={edit} />}
            {section !== WORKER_SETS && sectionRows.map((row) => {
              const raw = drafts[row.key]?.raw ?? readEditorValue(row);
              const optionValues = (row.choice_options ?? []).map((option) => option.value);
              const isTyped = Boolean(row.is_open_choice) && (typedKeys.includes(row.key) || !optionValues.includes(String(raw)));
              return (
                <div key={row.key} className={`setting-row${drafts[row.key] ? " edited" : ""}`}>
                  <div className="setting-text">
                    <div className="setting-name">
                      <code>{row.key.split(".").slice(1).join(".") || row.key}</code>
                      {/* a built-in or shipped default value shows no mark; only a value the owner's own file or a profile
                          changes does, and a change not saved yet is its own mark */}
                      {drafts[row.key]
                        ? <span className="pill warn" title="typed here, not saved yet">unsaved</span>
                        : isOwnerChange(row.source) && (
                          <span className={`pill${profile && row.source === profile ? " running" : ""}`}
                            title={profile && row.source === profile ? `set by profile ${profile}` : "set in your own settings file"}>changed</span>
                        )}
                    </div>
                    {row.comment && <p className="muted setting-comment">{renderComment(row.comment)}</p>}
                  </div>
                  <div className={`setting-control ${row.choice_options ? "choice" : row.kind}`}>
                    {row.kind === "bool" ? (
                      <input type="checkbox" aria-label={row.key} checked={Boolean(raw)} onChange={(event) => edit(row, event.target.checked)} />
                    ) : row.choice_options ? (
                      <>
                        <OptionMenu name={row.key} options={row.choice_options} value={String(raw)}
                          host={readModelHost(row)}
                          onOther={row.is_open_choice ? () => setTyped(row.key, true) : undefined}
                          onChoose={(value, option) => {
                            setTyped(row.key, false);
                            edit(row, value);
                            // a model chosen under another host runs on that host: the model's host moves with it
                            const hostRow = findHostRow(row);
                            if (option.host === undefined || !hostRow || option.host === readModelHost(row)) return;
                            edit(hostRow, option.host === row.default_host ? "" : option.host);
                          }} />
                        {isTyped && (
                          <input type="text" aria-label={`${row.key}, your own value`} autoFocus value={String(raw)}
                            placeholder="type a value" onChange={(event) => edit(row, event.target.value)} />
                        )}
                      </>
                    ) : row.choices && row.choices.length > 0 ? (
                      <OptionMenu name={row.key} value={String(raw)} onChoose={(value) => edit(row, value)}
                        options={row.choices.map((choice) => ({ value: choice, label: choice, hint: "", group: "" }))} />
                    ) : row.kind === "long-text" ? (
                      <textarea aria-label={row.key} value={String(raw ?? "")} rows={4}
                        placeholder={describeRunValue(row)} onChange={(event) => edit(row, event.target.value)} />
                    ) : row.kind === "json" ? (
                      <textarea aria-label={row.key} value={String(raw)} spellCheck={false} wrap="off"
                        rows={Math.min(16, String(raw).split("\n").length)} onChange={(event) => edit(row, event.target.value)} />
                    ) : (
                      <input type={row.kind === "number" ? "number" : "text"} aria-label={row.key} value={String(raw)}
                        placeholder={row.value === null || row.value === "" ? describeRunValue(row) : undefined}
                        onChange={(event) => edit(row, event.target.value)} />
                    )}
                    {row.kind === "json" && drafts[row.key] && !isJson(String(raw)) && (
                      <small className="error">not valid JSON</small>
                    )}
                  </div>
                </div>
              );
            })}
          </details>
        );
      })}
    </section>
  );
}
