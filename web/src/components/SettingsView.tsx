import { useEffect, useState } from "react";
import { describeError, fetchJson, removeSetting, saveSetting } from "../api";
import type { MissingProvider, SettingRow } from "../api";
import { asTree, listJobs, MODELS_KEY, PHASE_MODELS_KEY, readPath } from "../modelLibrary";
import { findSectionGroup, findSubgroupTitle, isSettingHidden, readSettingHelp, readSettingLabel, readSettingPrefix, SECTION_GROUPS,
  SECTION_SUBGROUPS } from "../settingsLabels";
import { usePersistentState } from "../viewState";
import Icon from "./Icon";
import { buildNewModelRow, copyModelFields } from "./ModelLibrary";
import OptionMenu from "./OptionMenu";
import ServerCards, { countServerCards } from "./ServerCards";
import { useSettingsProfiles } from "./RunChoices";
import SettingRowItem, { readEditorValue } from "./SettingRowItem";
import type { SettingEditContext } from "./SettingRowItem";
import Spinner from "./Spinner";
import WorkerSetsPanel, { WORKER_SET_KEYS } from "./WorkerSetsPanel";

// A change typed but not saved yet: its row, and the editor's value as it stands (text, or a checkbox's state)
export type SettingDraft = { row: SettingRow; raw: string | boolean };
export type SettingDrafts = Record<string, SettingDraft>;

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

// The page's sections in setup order: the model servers with the models that run on each, the worker sets that link jobs to
// models, then the rest by the person's job (`SECTION_GROUPS`, the servers group's own rows standing in the first two)
const SERVERS = "servers";
const WORKER_SETS = "worker-sets";
const OTHER = "other";
const HOST_PREFIXES = ["hosts", "providers"];
const SECTION_INTROS: Record<string, string> = {
  [SERVERS]: "",
  [WORKER_SETS]: "",
  [OTHER]: "Settings no other section lists.",
};

type Section = { id: string; title: string; intro: string };

const SECTIONS: Section[] = [
  { id: SERVERS, title: "Model servers", intro: SECTION_INTROS[SERVERS] },
  { id: WORKER_SETS, title: "Worker sets", intro: SECTION_INTROS[WORKER_SETS] },
  ...SECTION_GROUPS.filter((group) => group.id !== "machines-and-models")
    .map((group) => ({ id: group.id, title: group.title, intro: group.intro })),
  { id: OTHER, title: "Other settings", intro: SECTION_INTROS[OTHER] },
];

// The section a setting stands in; the models, the worker sets' links and the fast workflow's models have panels of their
// own and never a row in a section
function readSectionId(row: SettingRow, jobKeys: Set<string>): string {
  const prefix = readSettingPrefix(row.key);
  if (prefix === MODELS_KEY) return SERVERS;
  if (WORKER_SET_KEYS.has(row.key) || prefix === PHASE_MODELS_KEY || jobKeys.has(row.key)) return WORKER_SETS;
  if (HOST_PREFIXES.includes(prefix)) return SERVERS;
  return findSectionGroup(row.key)?.id ?? OTHER;
}

// "framesProcessing" as "Frames processing"
function renderPrefixTitle(prefix: string): string {
  const words = prefix.replace(/([A-Z])/g, " $1").toLowerCase();
  return words === "pdf" ? "PDF" : words.charAt(0).toUpperCase() + words.slice(1);
}

type SettingCard = { title: string; rows: SettingRow[] };

// The cards a section's settings stand in, each titled by what its settings do: the section's own list of cards when it has
// one (the settings no card lists in a last card), else one card a first part of the keys. A card is never titled like its
// section: a section of one such group takes the title "<section>: settings"
export function listSettingCards(section: { id: string; title: string }, rows: SettingRow[]): SettingCard[] {
  const cards: SettingCard[] = [];
  const put = (title: string, row: SettingRow) => {
    const card = cards.find((each) => each.title === title);
    if (card) card.rows.push(row);
    else cards.push({ title, rows: [row] });
  };
  for (const row of rows) {
    const title = findSubgroupTitle(section.id, row.key)
      ?? (SECTION_SUBGROUPS[section.id] ? `More ${section.title.toLowerCase()} settings` : renderPrefixTitle(readSettingPrefix(row.key)));
    put(title.toLowerCase() === section.title.toLowerCase() ? `${section.title}: settings` : title, row);
  }
  // the cards stand in the order the section lists them
  const order = (SECTION_SUBGROUPS[section.id] ?? []).map((group) => group.title);
  return cards.sort((first, second) => {
    const rank = (card: SettingCard) => order.indexOf(card.title) < 0 ? order.length : order.indexOf(card.title);
    return rank(first) - rank(second);
  });
}

// A setting's words matched by a search: its label, its help, its key and the program's comment
function matchesSearch(row: SettingRow, needle: string): boolean {
  return !needle || [readSettingLabel(row.key), readSettingHelp(row.key), row.key, row.comment]
    .some((text) => text.toLowerCase().includes(needle));
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
  // Every section closed until opened, and kept open when the settings are left and opened again; a search opens each
  // section holding a match
  const [openSections, setOpenSections] = usePersistentState<string[]>("settingsSections", []);
  // The cards inside a section stay closed until opened too, and keep their state like the sections
  const [openCards, setOpenCards] = usePersistentState<string[]>("settingsCards", []);
  // The rows whose value is being typed in a box, after "Other…" was picked from their list
  const [typedKeys, setTypedKeys] = useState<string[]>([]);
  // The file chosen in the Edit menu while changes were unsaved, waiting for the person to keep or drop them
  const [pendingProfile, setPendingProfile] = useState<string | null>(null);
  // Counts the reads the page asked for itself (after a model was taken out), beside the app's own `version`
  const [reloads, setReloads] = useState(0);
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
  }, [profile, version, reloads, onMessage]);

  const edit = (row: SettingRow, raw: string | boolean) => onDraft(row.key, raw === readEditorValue(row) ? null : { row, raw });
  const needle = filter.trim().toLowerCase();
  const allRows = rows ?? [];
  const jobKeys = new Set(listJobs(allRows).map((job) => job.key));
  // a model added and not saved yet stands among the rows, as a draft of its own
  const newRows = Object.values(drafts).map((draft) => draft.row).filter((row) => row.source === "new");
  const visible = allRows.filter((row) => !isSettingHidden(row.key) && !row.key.startsWith("_"));
  const setTyped = (key: string, isTyped: boolean) =>
    setTypedKeys((current) => isTyped ? [...new Set([...current, key])] : current.filter((name) => name !== key));
  const context: SettingEditContext = { rows: allRows, drafts, profile, edit, typedKeys, setTyped };

  // The fields of a model added and not saved yet are one draft, its whole entry; a field typed on its card rewrites it
  const addModel = (name: string, source: string, host: string) => onDraft(`${MODELS_KEY}.${name}`, {
    row: buildNewModelRow(name, source), raw: JSON.stringify(copyModelFields(allRows, source, host), null, 2),
  });
  const editNewModel = (name: string, field: string, value: unknown) => {
    const key = `${MODELS_KEY}.${name}`;
    const draft = drafts[key];
    if (!draft) return;
    let fields: Record<string, unknown> = {};
    try {
      const parsed: unknown = JSON.parse(String(draft.raw));
      fields = asTree(parsed);
    } catch {
      // the page writes this draft itself, so an unreadable one starts the copy again from its fields
      fields = {};
    }
    const { [field]: _old, ...others } = fields;
    onDraft(key, { row: draft.row, raw: JSON.stringify(value === null || value === "" ? others : { ...others, [field]: value }, null, 2) });
  };
  const removeModel = async (name: string) => {
    try {
      const answer = await removeSetting([MODELS_KEY, name], profile);
      onMessage(answer.output, answer.exit_code !== 0);
      if (answer.exit_code === 0) setReloads((count) => count + 1);
    } catch (error) {
      onMessage(`${name}: ${describeError(error)}`, true);
    }
  };

  const modelNames = [...new Set(allRows.filter((row) => readPath(row)[0] === MODELS_KEY && readPath(row).length === 3)
    .map((row) => readPath(row)[1]))];
  const setNames = Object.keys(asTree(allRows.find((row) => row.key === "workerSets")?.value));
  const sectionRows = (id: string) => visible.filter((row) => readSectionId(row, jobKeys) === id && matchesSearch(row, needle));
  // The servers (with their models) and the worker sets stand on their own panels, so they show whenever the settings hold
  // them; a section of rows shows when it has a row, and a search keeps the sections holding a match
  const serverNames = Object.keys(asTree(allRows.find((row) => row.key === "hosts")?.value));
  const isShown = (id: string) => {
    if (id === SERVERS) return (serverNames.length + modelNames.length + newRows.length > 0) && (!needle || "model servers".includes(needle)
      || serverNames.some((name) => name.toLowerCase().includes(needle)) || modelNames.some((name) => name.toLowerCase().includes(needle)));
    if (id === WORKER_SETS) return allRows.some((row) => row.key === "workerSets")
      && (!needle || "worker sets".includes(needle) || sectionRows(id).length > 0 || setNames.some((name) => name.toLowerCase().includes(needle)));
    return sectionRows(id).length > 0;
  };
  // the number beside a title counts the cards the section shows
  const countOf = (id: string): number => id === SERVERS ? countServerCards(allRows, context, newRows)
    : id === WORKER_SETS ? setNames.length + 1
    : listSettingCards(SECTIONS.find((section) => section.id === id) ?? { id, title: id }, sectionRows(id)).length;
  const shownSections = SECTIONS.filter((section) => isShown(section.id));

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
          placeholder="Find a setting" onChange={(event) => setFilter(event.target.value)} />
      </header>
      <p className="muted">A change stays marked until you press Save settings in the top bar. A run already going keeps its
        values; the next run uses the saved ones.</p>
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
                  <button className="link-button" onClick={() => setFilter(row.key)}>{readSettingLabel(row.key)}</button>
                </span>
              ))}. Every other value is the default settings'.</>}
          </p>
        );
      })()}
      {rows === null && <Spinner />}
      {rows !== null && needle && shownSections.length === 0 && (
        <p className="muted">No setting's name, help or key holds "{filter.trim()}".</p>
      )}
      {rows !== null && shownSections.map((section) => {
        const inSection = sectionRows(section.id);
        const cards = listSettingCards(section, inSection);
        return (
          <details key={section.id} className="settings-section" aria-label={section.title}
            open={Boolean(needle) || openSections.includes(section.id)}
            onToggle={(event) => {
              if (needle) return;
              const isOpen = event.currentTarget.open;
              setOpenSections((current) => isOpen ? [...new Set([...current, section.id])] : current.filter((name) => name !== section.id));
            }}>
            <summary><span className="caret"><Icon name="chevron" size={13} /></span><h3>{section.title}</h3>
              <span className="muted">{countOf(section.id)}</span></summary>
            {section.intro && <p className="muted settings-section-intro">{section.intro}</p>}
            {section.id === SERVERS && (
              <ServerCards rows={allRows} context={context} newRows={newRows} onAdd={addModel}
                onDiscard={(name) => onDraft(`${MODELS_KEY}.${name}`, null)} onEditNew={editNewModel} onRemove={removeModel} />
            )}
            {section.id === WORKER_SETS && <WorkerSetsPanel rows={allRows} drafts={drafts} onEdit={edit} />}
            {section.id !== SERVERS && section.id !== WORKER_SETS && cards.map((card) => (
              // a card never repeats its section's title: the settings are split by what they do, and each card is closed until
              // opened, like the section, so an opened section shows its card titles alone
              <details key={card.title} className="settings-prefix-group" aria-label={card.title}
                open={Boolean(needle) || openCards.includes(`${section.id}/${card.title}`)}
                onToggle={(event) => {
                  if (needle) return;
                  const isOpen = event.currentTarget.open;
                  const id = `${section.id}/${card.title}`;
                  setOpenCards((current) => isOpen ? [...new Set([...current, id])] : current.filter((name) => name !== id));
                }}>
                <summary><span className="caret"><Icon name="chevron" size={13} /></span><h4 className="settings-prefix">{card.title}</h4>
                  <span className="muted">{card.rows.length}</span></summary>
                {card.rows.map((row) => <SettingRowItem key={row.key} row={row} context={context} />)}
              </details>
            ))}
          </details>
        );
      })}
    </section>
  );
}
