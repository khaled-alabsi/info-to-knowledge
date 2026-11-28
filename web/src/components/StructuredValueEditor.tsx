import { useState } from "react";
import { Menu } from "./Dropdown";
import Icon from "./Icon";
import { listFixedChoices, PROVIDER_JOB_CHOICES, readSettingLabel, TEXT_READER_LANGUAGES } from "../settingsLabels";
import type { FixedChoice } from "../settingsLabels";
import OptionMenu from "./OptionMenu";

// A setting whose value is a list or a map, edited as a small table: a row a name or an item, a box for each value, a
// button to take a row out and one to add a row. Nobody types JSON: the draft the page keeps is the table's value, which
// the page writes as JSON itself.

type Tree = Record<string, unknown>;

// The columns of a table of objects when the value is empty and shows none, and what each column is called
const COLUMN_HINTS: Record<string, string[]> = { hosts: ["url", "apiKeyEnv", "kinds"], "tags.nameRules": ["prefix", "tag"] };
const COLUMN_TITLES: Record<string, string> = {
  url: "Address", apiKeyEnv: "Token (environment variable name)", kinds: "Jobs it can do", prefix: "Name starts with", tag: "Tag",
  name: "Name", value: "Value",
};
// What an empty box of a column shows as its example
const COLUMN_EXAMPLES: Record<string, string> = { apiKeyEnv: "e.g. OPENROUTER_API_KEY; empty: no login" };
// The columns whose value is a server's jobs, picked by tick boxes
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
  // A number is typed in a text box: a number box shows the browser's own decimal sign (a comma in a German browser),
  // so 0.95 read "0,95" beside boxes that show a dot. The typed text is kept while it is not a number yet ("0.")
  const [typed, setTyped] = useState<string | null>(null);
  if (typeof value === "boolean") {
    return <input type="checkbox" aria-label={label} checked={value} onChange={(event) => onChange(event.target.checked)} />;
  }
  if (typeof value === "number") {
    return <input type="text" inputMode="decimal" aria-label={label} placeholder={example}
      value={typed !== null && Number(typed) === value ? typed : printScalar(value)}
      onChange={(event) => { setTyped(event.target.value); onChange(parseTyped(event.target.value, value)); }} />;
  }
  return <input type="text" aria-label={label} placeholder={example} value={printScalar(value)}
    onChange={(event) => onChange(parseTyped(event.target.value, value))} />;
}

// A value of a named setting: a menu when the setting is chosen from a fixed list (a speed preset's video workflow), else a box
function ScalarOrChoice({ settingName, value, label, onChange }: {
  settingName: string; value: unknown; label: string; onChange: (next: unknown) => void;
}) {
  const fixed = typeof value === "string" ? listFixedChoices(settingName, value) : null;
  if (!fixed) return <ScalarBox value={value} label={label} onChange={onChange} />;
  return <OptionMenu name={label} value={String(value)} options={fixed.options.map((option) => ({ ...option, group: "" }))}
    onChoose={(next) => onChange(next)} />;
}

// A list of values ticked from a fixed list, one tick box each with its plain name (the languages the text reader reads); a
// value the file holds that the list does not stays ticked under its own name
function ChoiceTicks({ choices, value, label, onChange }: {
  choices: FixedChoice[]; value: unknown[]; label: string; onChange: (next: string[]) => void;
}) {
  const ticked = value.map(String);
  const all = [...choices, ...ticked.filter((each) => !choices.some((choice) => choice.value === each))
    .map((each) => ({ value: each, label: each, hint: "" }))];
  return (
    <div className="suits">
      {all.map((choice) => (
        <label key={choice.value} className="suit-choice">
          <input type="checkbox" aria-label={`${label}: ${choice.label}`} checked={ticked.includes(choice.value)}
            onChange={(event) => onChange(all.map((each) => each.value).filter((each) =>
              each === choice.value ? event.target.checked : ticked.includes(each)))} />
          {choice.label}
        </label>
      ))}
    </div>
  );
}

// The jobs a server can do, one tick box a job with its plain name; written back as the ticked jobs' stored words
function JobTicks({ value, label, onChange }: { value: unknown; label: string; onChange: (next: string[]) => void }) {
  const ticked = Array.isArray(value) ? value.map(String) : [];
  return (
    <div className="suits">
      {PROVIDER_JOB_CHOICES.map((choice) => (
        <label key={choice.value} className="suit-choice">
          <input type="checkbox" aria-label={`${label}: ${choice.label}`} checked={ticked.includes(choice.value)}
            onChange={(event) => onChange(PROVIDER_JOB_CHOICES.map((each) => each.value).filter((each) =>
              each === choice.value ? event.target.checked : ticked.includes(each)))} />
          {choice.label}
        </label>
      ))}
    </div>
  );
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
      <input type="text" aria-label={`${label}: new value`} placeholder="Value" value={text}
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
  temperature: "Randomness", top_p: "Word choice spread", top_k: "Number of word choices", min_p: "Least likely word allowed",
  repeat_penalty: "Repeat penalty",
  presence_penalty: "Presence penalty", frequency_penalty: "Frequency penalty", seed: "Seed",
};

// A map of single values: one labelled field a value, never a table of names and values. A map with a fixed set of names (a
// model's sampling) adds a value from a menu of those names; another map adds one by the name typed
function ScalarMapTable({ value, label, choices, onChange }: {
  value: Tree; label: string; choices?: Record<string, string>; onChange: (next: Tree) => void;
}) {
  const names = Object.keys(value);
  const addable = choices ? Object.entries(choices).filter(([name]) => !names.includes(name)) : [];
  return (
    <div className="structured-fields">
      {names.map((name) => (
        <div key={name} className="structured-field">
          <span className="setting-label">{readRowName(name)}</span>
          <ScalarOrChoice settingName={name} value={value[name]} label={`${label}: ${readRowName(name)}`}
            onChange={(next) => onChange({ ...value, [name]: next })} />
          <TrashButton what={`${readRowName(name)} from ${label}`}
            onClick={() => onChange(Object.fromEntries(names.filter((other) => other !== name).map((other) => [other, value[other]])))} />
        </div>
      ))}
      {names.length === 0 && <p className="muted">Nothing listed yet.</p>}
      {choices
        ? addable.length > 0 && (
          <Menu label="Add an option" icon="plus" variant="ghost"
            items={addable.map(([name, title]) => ({ label: title, icon: "plus", hint: "starts at 0",
              onSelect: () => onChange({ ...value, [name]: 0 }) }))} />
        )
        : <NameBox label={label} placeholder="Setting name" taken={names} onAdd={(name, text) => onChange({ ...value, [name]: parseNew(text) })} />}
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
                  {LIST_COLUMNS.has(column)
                    ? <JobTicks value={value[each][column]} label={`${label}: ${each} ${readColumnTitle(column)}`}
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
  if (Array.isArray(value) && settingKey === "frames.textReaderLanguages") {
    return <ChoiceTicks choices={TEXT_READER_LANGUAGES} value={value} label={label} onChange={onChange} />;
  }
  if (Array.isArray(value)) {
    if (value.every(isScalar) && hint.length === 0) {
      return <TextListTable value={value} label={label} onChange={onChange} />;
    }
    return <ObjectListTable value={value.filter(isTree)} label={label} hint={hint} onChange={onChange} />;
  }
  if (isTree(value)) {
    const entries = Object.values(value);
    if (entries.length > 0 ? entries.every(isScalar) : hint.length === 0) {
      return <ScalarMapTable value={value} label={label} choices={settingKey.endsWith(".sampling") ? SAMPLING_NAMES : undefined}
        onChange={onChange} />;
    }
    if (hint.length > 0 || entries.some((entry) => isTree(entry) && Object.values(entry).some((field) => !isScalar(field)))) {
      return <ObjectMapTable value={value as Record<string, Tree>} label={label} hint={hint} onChange={onChange} />;
    }
    return <NestedMapBlocks value={value as Record<string, Tree>} label={label} onChange={onChange} />;
  }
  return <ScalarBox value={value} label={label} onChange={onChange} />;
}
