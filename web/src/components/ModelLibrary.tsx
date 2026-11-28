import { useState } from "react";
import type { SettingRow } from "../api";
import { asTree, describeModelReplies, describeModelRun, describeModelUses, isModelRemovable, isTree, listModelNames, listModelUses, MODELS_KEY, readModelRows,
  readPath, SETS_KEY } from "../modelLibrary";
import type { ModelUse, Tree } from "../modelLibrary";
import { ADVANCED_MODEL_FIELDS, isSettingHidden, readSettingHelp, readSettingLabel } from "../settingsLabels";
import Icon from "./Icon";
import OptionMenu from "./OptionMenu";
import SettingRowItem from "./SettingRowItem";
import type { SettingEditContext } from "./SettingRowItem";

// The models of one model server (or of no listed server), one card each with which model it is, how much it reads at once
// and its other settings: opening a card shows them, the ones few people change under "Advanced". It stands inside its
// server's card on the page, so the card never asks for the server. A model is added as a copy of another, copied from its
// card, and removed from its card when the owner's own file holds it. Nothing here is attached to a job: the worker sets
// link jobs to these models by name.

const MAIN_FIELDS = ["spec", "suits", "framesPerCall"];
// A model on no listed server shows its server field, so it can be given one
const MAIN_FIELDS_NO_SERVER = ["host", ...MAIN_FIELDS];

// The fields a model has, copied from `source`: every field a settings file sets for it, so the copy reads as it does; the
// copy runs on `host` when one is given
export function copyModelFields(rows: SettingRow[], source: string, host = ""): Tree {
  const fields = Object.values(readModelRows(rows, source));
  const setFields = fields.filter((row) => row.source !== "code");
  const copied: Tree = Object.fromEntries((setFields.length ? setFields : fields.filter((row) => readPath(row)[2] === "spec"))
    .map((row) => [readPath(row)[2], row.value]));
  return host ? { ...copied, host } : copied;
}

// The row a new model stands on until it is saved: its whole entry, written by the page from the fields on its card
export function buildNewModelRow(name: string, source: string): SettingRow {
  return { key: `${MODELS_KEY}.${name}`, path: [MODELS_KEY, name], value: null, kind: "json", source: "new",
    comment: `A new model, copied from ${source}.` };
}

// A typed value kept as the field's type: a number stays a number, an empty number box takes the field out
function parseField(row: SettingRow, raw: string | boolean): unknown {
  if (row.kind === "bool") return raw;
  const text = String(raw);
  if (row.kind === "number") return text.trim() === "" ? null : Number(text);
  if (row.kind === "json") {
    try {
      return JSON.parse(text);
    } catch {
      // the table writes valid JSON itself, so an unreadable text keeps the value as it was
      return row.value;
    }
  }
  return text;
}

// The model's name on its provider, a plain text box. The spec the settings hold is "<client>:<name>"; the box shows the name
// and writes the spec back with the client kept (`openai-compatible` for a model that has none), so nobody reads or types the
// joined text
function ModelNameRow({ row, context }: { row: SettingRow; context: SettingEditContext }) {
  const spec = String(context.drafts[row.key]?.raw ?? row.value ?? "");
  const colon = spec.indexOf(":");
  const client = colon < 0 ? "openai-compatible" : spec.slice(0, colon);
  const modelName = colon < 0 ? spec : spec.slice(colon + 1);
  return (
    <div className={`setting-row${context.drafts[row.key] ? " edited" : ""}`}>
      <div className="setting-text">
        <div className="setting-name"><span className="setting-label">{readSettingLabel(row.key)}</span></div>
        <p className="muted setting-comment">{readSettingHelp(row.key)}</p>
      </div>
      <div className="setting-control text">
        <input type="text" aria-label={`${readSettingLabel(row.key)} of ${row.path?.[1] ?? ""}`} value={modelName}
          onChange={(event) => context.edit(row, `${client}:${event.target.value}`)} />
      </div>
    </div>
  );
}

// The kinds of work a model can be offered for, as the page says them, by the word the settings hold
const SUIT_CHOICES: { value: string; label: string }[] = [
  { value: "frames", label: "Reading pictures" },
  { value: "writing", label: "Writing the document" },
  { value: "filtering", label: "Filtering frames" },
];

// The jobs a model suits, as one tick box each; none ticked offers the model for every job
function SuitsRow({ row, context }: { row: SettingRow; context: SettingEditContext }) {
  const draft = context.drafts[row.key];
  let current: unknown = row.value;
  if (draft) {
    try {
      current = JSON.parse(String(draft.raw));
    } catch {
      // a draft the page wrote itself is always valid; an unreadable one leaves the saved value
    }
  }
  const ticked = Array.isArray(current) ? current.map(String) : [];
  return (
    <div className={`setting-row${draft ? " edited" : ""}`}>
      <div className="setting-text">
        <div className="setting-name"><span className="setting-label">{readSettingLabel(row.key)}</span></div>
        <p className="muted setting-comment">{readSettingHelp(row.key)}</p>
      </div>
      <div className="setting-control suits">
        {SUIT_CHOICES.map((choice) => (
          <label key={choice.value} className="suit-choice">
            <input type="checkbox" aria-label={`${choice.label}, ${row.path?.[1] ?? ""}`} checked={ticked.includes(choice.value)}
              onChange={(event) => context.edit(row, JSON.stringify(SUIT_CHOICES.map((each) => each.value).filter((value) =>
                value === choice.value ? event.target.checked : ticked.includes(value))))} />
            {choice.label}
          </label>
        ))}
      </div>
    </div>
  );
}

function ModelCard({ name, rows, context, isNew, drafted, sourceRows, users, isConfirming, hasServer, onCopy, onAsk, onCancel,
  onRemove, onEditNew }: {
  name: string;
  // the card stands in its server's card, which names the server
  hasServer: boolean;
  rows: SettingRow[];
  context: SettingEditContext;
  isNew: boolean;
  drafted?: Tree;
  sourceRows?: Record<string, SettingRow>;
  users: ModelUse[];
  isConfirming: boolean;
  onCopy: () => void;
  onAsk: () => void;
  onCancel: () => void;
  onRemove: () => void;
  onEditNew: (field: string, value: unknown) => void;
}) {
  const fields = isNew && sourceRows
    ? Object.fromEntries(Object.entries(sourceRows).map(([field, row]) => [field, {
      ...row, key: `${MODELS_KEY}.${name}.${field}`, path: [MODELS_KEY, name, field],
      value: drafted && field in drafted ? drafted[field] : row.value, source: drafted && field in drafted ? "new" : "code",
      chosen_host: undefined,
    } as SettingRow]))
    : readModelRows(rows, name);
  const cardContext: SettingEditContext = isNew
    ? { ...context, rows: Object.values(fields), drafts: {}, edit: (row, raw) => onEditNew(readPath(row)[2], parseField(row, raw)) }
    : context;
  const shown = (field: string) => fields[field] && !isSettingHidden(fields[field].key);
  const mainFields = hasServer ? MAIN_FIELDS : MAIN_FIELDS_NO_SERVER;
  const main = mainFields.filter(shown);
  const advanced = Object.keys(fields).filter((field) => !MAIN_FIELDS_NO_SERVER.includes(field) && shown(field))
    .sort((first, second) => ADVANCED_MODEL_FIELDS.indexOf(first) - ADVANCED_MODEL_FIELDS.indexOf(second));
  const run = describeModelRun(rows, name, isNew ? drafted : undefined);
  const replies = describeModelReplies(rows, name, isNew ? drafted : undefined);
  const isEdited = Object.keys(context.drafts).some((key) => key.startsWith(`${MODELS_KEY}.${name}.`));
  const isRemovable = isNew || isModelRemovable(rows, name);
  return (
    <details className={`model-card${isNew || isEdited ? " edited" : ""}`} role="group" aria-label={`model ${name}`} open={isNew || undefined}>
      <summary>
        <span className="model-card-name">
          <strong>{name}</strong>
          {(isNew || isEdited) && <span className="pill warn">unsaved</span>}
        </span>
        <span className="muted model-card-run">{hasServer ? "" : `${run.host} · `}{run.model} · reads {run.context} at once</span>
        {replies && <span className="muted model-card-run">{replies}</span>}
      </summary>
      <div className="model-card-body">
        {users.length > 0 && <p className="muted">Used by: {describeModelUses(users)}</p>}
        {main.map((field) => field === "spec"
          ? <ModelNameRow key={field} row={fields[field]} context={cardContext} />
          : field === "suits"
            ? <SuitsRow key={field} row={fields[field]} context={cardContext} />
            : <SettingRowItem key={field} row={fields[field]} context={cardContext} />)}
        {advanced.length > 0 && (
          <details className="model-advanced">
            <summary>Advanced</summary>
            {advanced.map((field) => <SettingRowItem key={field} row={fields[field]} context={cardContext} />)}
          </details>
        )}
        <div className="model-card-actions">
          <button type="button" className="btn" onClick={onCopy} aria-label={`copy model ${name}`}><Icon name="copy" size={14} /> Copy</button>
          {isNew ? (
            <button type="button" className="btn danger" onClick={onCancel} aria-label={`discard new model ${name}`}>
              <Icon name="trash" size={14} /> Discard</button>
          ) : isRemovable ? (
            isConfirming ? (
              <>
                <span>Remove {name} from your settings?{users.length > 0 ? ` The worker sets ${users.map((use) => use.set).join(", ")} call it.` : ""}</span>
                <button type="button" className="btn danger" onClick={onRemove} aria-label={`yes, remove model ${name}`}>Yes, remove</button>
                <button type="button" className="btn" onClick={onCancel}>Keep it</button>
              </>
            ) : (
              <button type="button" className="btn danger" onClick={onAsk} aria-label={`remove model ${name}`}>
                <Icon name="trash" size={14} /> Remove</button>
            )
          ) : (
            <small className="muted">A model of the shipped settings: it can be changed, not removed.</small>
          )}
        </div>
      </div>
    </details>
  );
}

export default function ModelLibrary({ rows, context, host, names, newRows, takenNames, onAdd, onDiscard, onEditNew, onRemove }: {
  rows: SettingRow[];
  context: SettingEditContext;
  // the server these models run on; "" for the models no listed server runs
  host: string;
  // the saved models of this server
  names: string[];
  // the rows of the models of this server added and not saved yet
  newRows: SettingRow[];
  // every model's name on the page, saved or new: a copy may start from any of them, and a new name must be none of them
  takenNames: string[];
  onAdd: (name: string, source: string, host: string) => void;
  onDiscard: (name: string) => void;
  onEditNew: (name: string, field: string, value: unknown) => void;
  onRemove: (name: string) => void;
}) {
  const [form, setForm] = useState<{ name: string; source: string } | null>(null);
  const [asking, setAsking] = useState("");
  const savedNames = names;
  const taken = new Set(takenNames);
  const name = form?.name.trim() ?? "";
  const sets = asTree(rows.find((row) => row.key === SETS_KEY)?.value);
  const add = () => {
    if (!form || !name || taken.has(name)) return;
    onAdd(name, form.source, host);
    setForm(null);
  };
  const hasServer = host !== "";
  // a copy may start from any saved model of any server
  const sources = listModelNames(rows);
  const where = host || "no server";
  return (
    <div className="model-library">
      {newRows.map((row) => {
        const newName = readPath(row)[1];
        const draft = context.drafts[row.key];
        let drafted: Tree = {};
        try {
          const parsed: unknown = JSON.parse(String(draft?.raw ?? "{}"));
          drafted = isTree(parsed) ? parsed : {};
        } catch {
          // the page writes this draft itself, so an unreadable one stands as an empty copy
          drafted = {};
        }
        const source = /copied from (.+)\.$/.exec(row.comment)?.[1] ?? "";
        return (
          <ModelCard key={row.key} name={newName} rows={rows} context={context} isNew drafted={drafted} hasServer={hasServer}
            sourceRows={readModelRows(rows, source)} users={[]} isConfirming={false}
            onCopy={() => setForm({ name: "", source: newName })} onAsk={() => undefined}
            onCancel={() => onDiscard(newName)} onRemove={() => undefined}
            onEditNew={(field, value) => onEditNew(newName, field, value)} />
        );
      })}
      {savedNames.map((each) => (
        <ModelCard key={each} name={each} rows={rows} context={context} isNew={false} hasServer={hasServer}
          users={listModelUses(rows, sets, each)} isConfirming={asking === each}
          onCopy={() => setForm({ name: "", source: each })} onAsk={() => setAsking(each)} onCancel={() => setAsking("")}
          onRemove={() => { setAsking(""); onRemove(each); }} onEditNew={() => undefined} />
      ))}
      {form === null ? (
        <div className="setting-row add-model">
          <button type="button" className="btn" onClick={() => setForm({ name: "", source: sources[0] ?? "" })}
            aria-label={`add model to ${where}`} disabled={sources.length === 0}>+ Add model</button>
        </div>
      ) : (
        <div className="setting-row edited add-model">
          <input type="text" aria-label={`New model's name on ${where}`} placeholder="name, as frame-other-mac" autoFocus
            value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })}
            onKeyDown={(event) => { if (event.key === "Enter") add(); }} />
          <OptionMenu name={`Copy of, for ${where}`} prefix="Copy of" value={form.source}
            onChoose={(source) => setForm({ ...form, source })}
            options={sources.map((each) => ({ value: each, label: each, hint: describeModelRun(rows, each).host, group: "" }))} />
          <button type="button" className="btn primary" onClick={add} disabled={!name || taken.has(name)}>Add</button>
          <button type="button" className="btn" onClick={() => setForm(null)}>Cancel</button>
          {taken.has(name) && <small className="error">a model has this name</small>}
        </div>
      )}
    </div>
  );
}
