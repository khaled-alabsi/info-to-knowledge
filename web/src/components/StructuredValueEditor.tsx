import { useState } from "react";
import Icon from "./Icon";
import { readSettingLabel } from "../settingsLabels";

// A setting whose value is a list or a map, edited as a small table: a row a name or an item, a box for each value, a
// button to take a row out and one to add a row. Nobody types JSON: the draft the page keeps is the table's value, which
// the page writes as JSON itself.

type Tree = Record<string, unknown>;

// The columns of a table of objects when the value is empty and shows none, and what each column is called
const COLUMN_HINTS: Record<string, string[]> = { hosts: ["url", "apiKeyEnv", "kinds"], "tags.nameRules": ["prefix", "tag"] };
const COLUMN_TITLES: Record<string, string> = {
  url: "Address", apiKeyEnv: "Token (environment variable name)", kinds: "Jobs", prefix: "Name starts with", tag: "Tag",
  name: "Name", value: "Value",
};
// What an empty box of a column shows as its example
const COLUMN_EXAMPLES: Record<string, string> = { apiKeyEnv: "e.g. OPENROUTER_API_KEY; empty: no login", kinds: "chat, speech" };
// The columns whose value is a list of words, typed as words with commas between them
const LIST_COLUMNS = new Set(["kinds"]);

function isTree(value: unknown): value is Tree {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isScalar(value: unknown): boolean {
  return value === null || ["string", "number", "boolean"].includes(typeof value);
}

// A value typed in a box keeps the type of the value it replaces: a number stays a number, true and false stay booleans
function parseTyped(text: string, like: unknown): unknown {
  if (typeof like === "number" && text.trim() !== "" && !Number.isNaN(Number(text))) return Number(text);
  if (typeof like === "boolean" && (text === "true" || text === "false")) return text === "true";
  return text;
}

// A value for a new row typed with nothing to copy the type from: a number when it reads as one
function parseNew(text: string): unknown {
  return text.trim() !== "" && !Number.isNaN(Number(text)) ? Number(text) : text;
}

function printScalar(value: unknown): string {
  return value === null || value === undefined ? "" : String(value);
}

function readColumnTitle(column: string): string {
  return COLUMN_TITLES[column] ?? column;
}

function ScalarBox({ value, label, example, onChange }: {
  value: unknown; label: string; example?: string; onChange: (next: unknown) => void;
}) {
  if (typeof value === "boolean") {
    return <input type="checkbox" aria-label={label} checked={value} onChange={(event) => onChange(event.target.checked)} />;
  }
  return <input type={typeof value === "number" ? "number" : "text"} aria-label={label} placeholder={example} value={printScalar(value)}
    onChange={(event) => onChange(parseTyped(event.target.value, value))} />;
}

// A list of words shown as "chat, speech" and written back as ["chat", "speech"]; the typed text is kept while the box
// is in use, so a comma or a space just typed is not removed under the cursor
function WordListBox({ value, label, example, onChange }: {
  value: unknown; label: string; example?: string; onChange: (next: string[]) => void;
}) {
  const words = Array.isArray(value) ? value.map(String) : [];
  const [typed, setTyped] = useState<string | null>(null);
  return <input type="text" aria-label={label} placeholder={example} value={typed ?? words.join(", ")}
    onChange={(event) => {
      setTyped(event.target.value);
      onChange(event.target.value.split(",").map((word) => word.trim()).filter(Boolean));
    }}
    onBlur={() => setTyped(null)} />;
}

// A red square trash icon that takes one row out; its title and accessible name say what it takes out
function TrashButton({ what, onClick }: { what: string; onClick: () => void }) {
  return (
    <button type="button" className="btn icon-only danger trash-button" title={`Remove ${what}`} aria-label={`remove ${what}`} onClick={onClick}>
      <Icon name="trash" size={14} />
    </button>
  );
}

function NameBox({ label, placeholder, onAdd, taken }: {
  label: string;
  placeholder: string;
  onAdd: (name: string, value: string) => void;
  taken: string[];
}) {
  const [name, setName] = useState("");
  const [text, setText] = useState("");
  const isTaken = taken.includes(name.trim());
  const add = () => {
    if (!name.trim() || isTaken) return;
    onAdd(name.trim(), text);
    setName("");
    setText("");
  };
  return (
    <div className="structured-add">
      <input type="text" aria-label={`${label}: new name`} placeholder={placeholder} value={name}
        onChange={(event) => setName(event.target.value)} />
      <input type="text" aria-label={`${label}: new value`} placeholder="value" value={text}
        onChange={(event) => setText(event.target.value)} />
      <button type="button" className="btn" onClick={add} disabled={!name.trim() || isTaken}>Add row</button>
      {isTaken && <small className="error">a row has this name</small>}
    </div>
  );
}

// A row's name in plain words: a map keyed by settings ("review.isOn" in a speed preset) shows each setting's label
function readRowName(name: string): string {
  return name.includes(".") ? readSettingLabel(name) : SAMPLING_NAMES[name] ?? name;
}

// A model's sampling values under their usual names, never the request's field names
const SAMPLING_NAMES: Record<string, string> = {
  temperature: "Temperature", top_p: "Top P", top_k: "Top K", min_p: "Min P", repeat_penalty: "Repeat penalty",
  presence_penalty: "Presence penalty", frequency_penalty: "Frequency penalty", seed: "Seed",
};

// A map of single values: one row a name, its value beside it
function ScalarMapTable({ value, label, onChange }: { value: Tree; label: string; onChange: (next: Tree) => void }) {
  const names = Object.keys(value);
  return (
    <div className="structured-table">
      <table>
        <thead><tr><th scope="col">Name</th><th scope="col">Value</th><th scope="col"><span className="sr-only">Remove</span></th></tr></thead>
        <tbody>
          {names.map((name) => (
            <tr key={name}>
              <th scope="row">{readRowName(name)}</th>
              <td><ScalarBox value={value[name]} label={`${label}: ${readRowName(name)}`}
                onChange={(next) => onChange({ ...value, [name]: next })} /></td>
              <td><TrashButton what={`${readRowName(name)} from ${label}`}
                onClick={() => onChange(Object.fromEntries(names.filter((other) => other !== name).map((other) => [other, value[other]])))} /></td>
            </tr>
          ))}
          {names.length === 0 && <tr><td colSpan={3} className="muted">Nothing listed yet.</td></tr>}
        </tbody>
      </table>
      <NameBox label={label} placeholder="name" taken={names} onAdd={(name, text) => onChange({ ...value, [name]: parseNew(text) })} />
    </div>
  );
}

// A list of texts: one box an item
function TextListTable({ value, label, onChange }: { value: unknown[]; label: string; onChange: (next: unknown[]) => void }) {
  const [draft, setDraft] = useState("");
  return (
    <div className="structured-table">
      <table>
        <tbody>
          {value.map((item, index) => (
            <tr key={index}>
              <td><ScalarBox value={item} label={`${label}: item ${index + 1}`}
                onChange={(next) => onChange(value.map((each, at) => at === index ? next : each))} /></td>
              <td><TrashButton what={`item ${index + 1} from ${label}`}
                onClick={() => onChange(value.filter((_, at) => at !== index))} /></td>
            </tr>
          ))}
          {value.length === 0 && <tr><td className="muted">Nothing listed yet.</td></tr>}
        </tbody>
      </table>
      <div className="structured-add">
        <input type="text" aria-label={`${label}: new item`} placeholder="new item" value={draft}
          onChange={(event) => setDraft(event.target.value)} />
        <button type="button" className="btn" disabled={!draft.trim()}
          onClick={() => { onChange([...value, draft.trim()]); setDraft(""); }}>Add row</button>
      </div>
    </div>
  );
}

// Columns of a table of objects: the ones the hint names, then any other key an object holds
function listColumns(rows: Tree[], hint: string[]): string[] {
  return [...new Set([...hint, ...rows.flatMap((row) => Object.keys(row))])];
}

// A list of objects (the naming rules): one row an object, one column a field
function ObjectListTable({ value, label, hint, onChange }: {
  value: Tree[];
  label: string;
  hint: string[];
  onChange: (next: Tree[]) => void;
}) {
  const columns = listColumns(value, hint);
  return (
    <div className="structured-table stacked">
      <table>
        <thead><tr>{columns.map((column) => <th key={column} scope="col">{readColumnTitle(column)}</th>)}<th scope="col"><span className="sr-only">Remove</span></th></tr></thead>
        <tbody>
          {value.map((item, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column} data-label={readColumnTitle(column)}><ScalarBox value={item[column] ?? ""} label={`${label}: row ${index + 1} ${readColumnTitle(column)}`}
                  onChange={(next) => onChange(value.map((each, at) => at === index ? { ...each, [column]: next } : each))} /></td>
              ))}
              <td><TrashButton what={`row ${index + 1} of ${label}`}
                onClick={() => onChange(value.filter((_, at) => at !== index))} /></td>
            </tr>
          ))}
          {value.length === 0 && <tr><td colSpan={columns.length + 1} className="muted">Nothing listed yet.</td></tr>}
        </tbody>
      </table>
      <button type="button" className="btn" onClick={() => onChange([...value, Object.fromEntries(columns.map((column) => [column, ""]))])}>
        Add row</button>
    </div>
  );
}

// A map of objects with the same fields (the hosts): one row a name, one column a field
function ObjectMapTable({ value, label, hint, onChange }: {
  value: Record<string, Tree>;
  label: string;
  hint: string[];
  onChange: (next: Record<string, Tree>) => void;
}) {
  const names = Object.keys(value);
  const columns = listColumns(Object.values(value), hint);
  const [name, setName] = useState("");
  const isTaken = names.includes(name.trim());
  return (
    <div className="structured-table stacked">
      <table>
        <thead><tr><th scope="col">Name</th>{columns.map((column) => <th key={column} scope="col">{readColumnTitle(column)}</th>)}<th scope="col"><span className="sr-only">Remove</span></th></tr></thead>
        <tbody>
          {names.map((each) => (
            <tr key={each}>
              <th scope="row">{each}</th>
              {columns.map((column) => (
                <td key={column} data-label={readColumnTitle(column)} className={`col-${column}`}>
                  {LIST_COLUMNS.has(column) || Array.isArray(value[each][column])
                    ? <WordListBox value={value[each][column]} example={COLUMN_EXAMPLES[column]} label={`${label}: ${each} ${readColumnTitle(column)}`}
                      onChange={(next) => onChange({ ...value, [each]: { ...value[each], [column]: next } })} />
                    : <ScalarBox value={value[each][column] ?? ""} example={COLUMN_EXAMPLES[column]} label={`${label}: ${each} ${readColumnTitle(column)}`}
                      onChange={(next) => onChange({ ...value, [each]: { ...value[each], [column]: next } })} />}
                </td>
              ))}
              <td><TrashButton what={`${each} from ${label}`}
                onClick={() => onChange(Object.fromEntries(names.filter((other) => other !== each).map((other) => [other, value[other]])))} /></td>
            </tr>
          ))}
          {names.length === 0 && <tr><td colSpan={columns.length + 2} className="muted">Nothing listed yet.</td></tr>}
        </tbody>
      </table>
      <div className="structured-add">
        <input type="text" aria-label={`${label}: new name`} placeholder="name of the new row" value={name}
          onChange={(event) => setName(event.target.value)} />
        <button type="button" className="btn" disabled={!name.trim() || isTaken}
          onClick={() => {
            onChange({ ...value, [name.trim()]: Object.fromEntries(columns.map((column) => [column, LIST_COLUMNS.has(column) ? [] : ""])) });
            setName("");
          }}>Add row</button>
        {isTaken && <small className="error">a row has this name</small>}
      </div>
    </div>
  );
}

// A map of maps of single values (the speed presets): a block a name, each block a table of its own
function NestedMapBlocks({ value, label, onChange }: {
  value: Record<string, Tree>;
  label: string;
  onChange: (next: Record<string, Tree>) => void;
}) {
  const names = Object.keys(value);
  const [name, setName] = useState("");
  const isTaken = names.includes(name.trim());
  return (
    <div className="structured-blocks">
      {names.map((each) => (
        <div key={each} className="structured-block">
          <div className="structured-block-head">
            <h5>{each}</h5>
            <TrashButton what={`${each} from ${label}`}
              onClick={() => onChange(Object.fromEntries(names.filter((other) => other !== each).map((other) => [other, value[other]])))} />
          </div>
          <ScalarMapTable value={value[each]} label={`${label}: ${each}`} onChange={(next) => onChange({ ...value, [each]: next })} />
        </div>
      ))}
      <div className="structured-add">
        <input type="text" aria-label={`${label}: new name`} placeholder="name of the new group" value={name}
          onChange={(event) => setName(event.target.value)} />
        <button type="button" className="btn" disabled={!name.trim() || isTaken}
          onClick={() => { onChange({ ...value, [name.trim()]: {} }); setName(""); }}>Add group</button>
        {isTaken && <small className="error">a group has this name</small>}
      </div>
    </div>
  );
}

// Edits a list or a map value as tables; `settingKey` names the setting so an empty table still has its columns
export default function StructuredValueEditor({ settingKey, label, value, onChange }: {
  settingKey: string;
  label: string;
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  const hint = COLUMN_HINTS[settingKey] ?? [];
  if (Array.isArray(value)) {
    if (value.every(isScalar) && hint.length === 0) {
      return <TextListTable value={value} label={label} onChange={onChange} />;
    }
    return <ObjectListTable value={value.filter(isTree)} label={label} hint={hint} onChange={onChange} />;
  }
  if (isTree(value)) {
    const entries = Object.values(value);
    if (entries.length > 0 ? entries.every(isScalar) : hint.length === 0) {
      return <ScalarMapTable value={value} label={label} onChange={onChange} />;
    }
    if (hint.length > 0 || entries.some((entry) => isTree(entry) && Object.values(entry).some((field) => !isScalar(field)))) {
      return <ObjectMapTable value={value as Record<string, Tree>} label={label} hint={hint} onChange={onChange} />;
    }
    return <NestedMapBlocks value={value as Record<string, Tree>} label={label} onChange={onChange} />;
  }
  return <ScalarBox value={value} label={label} onChange={onChange} />;
}
