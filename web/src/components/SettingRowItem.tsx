import type { ReactNode } from "react";
import type { SettingRow } from "../api";
import { describeRunValue } from "../settingRunValue";
import { readChoiceLabel, readSettingHelp, readSettingLabel } from "../settingsLabels";
import OptionMenu from "./OptionMenu";
import type { SettingDrafts } from "./SettingsView";
import StructuredValueEditor from "./StructuredValueEditor";

// The value an editor holds for a row: a checkbox's state, or text (JSON for lists and maps, which the page writes itself)
export function readEditorValue(row: SettingRow): string | boolean {
  if (row.kind === "bool") return Boolean(row.value);
  if (row.kind === "json") return JSON.stringify(row.value, null, 2);
  return row.value === null || row.value === undefined ? "" : String(row.value);
}

// What every row editor needs from the page: the rows (a model's host row is found beside its spec row), the drafts typed
// and not saved, the file being edited, and the list of rows whose value is being typed after "Other…" was picked
export type SettingEditContext = {
  rows: SettingRow[];
  drafts: SettingDrafts;
  profile: string;
  edit: (row: SettingRow, raw: string | boolean) => void;
  typedKeys: string[];
  setTyped: (key: string, isTyped: boolean) => void;
};

// A comment's `code` spans drawn as code, the rest as plain text
function renderComment(comment: string): ReactNode[] {
  return comment.split(/(`[^`]+`)/).map((part, index) =>
    part.startsWith("`") && part.endsWith("`") && part.length > 2 ? <code key={index}>{part.slice(1, -1)}</code> : part);
}

function parseJson(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    // only a parse error is expected: the draft is whatever was last written, and an unreadable one shows the saved value
    return undefined;
  }
}

// One setting as a line of the page: its plain label and a mark when changed, the help under it, and its editor
export default function SettingRowItem({ row, context, label }: { row: SettingRow; context: SettingEditContext; label?: string }) {
  const { rows, drafts, edit, typedKeys, setTyped } = context;
  const raw = drafts[row.key]?.raw ?? readEditorValue(row);
  const optionValues = (row.choice_options ?? []).map((option) => option.value);
  const isTyped = Boolean(row.is_open_choice) && (typedKeys.includes(row.key) || !optionValues.includes(String(raw)));
  // A model spec's sibling row naming the model's host (`models.<name>.host`), and the host in force: its draft when one is
  // chosen (empty meaning the host named like the provider), else the one the server says the model runs on
  const findHostRow = (specRow: SettingRow) => rows.find((candidate) => candidate.path?.length === 3
    && candidate.path[0] === specRow.path?.[0] && candidate.path[1] === specRow.path?.[1] && candidate.path[2] === "host");
  const readModelHost = (specRow: SettingRow): string | undefined => {
    if (specRow.chosen_host === undefined) return undefined;
    const hostRow = findHostRow(specRow);
    const draft = hostRow ? drafts[hostRow.key] : undefined;
    if (!draft) return specRow.chosen_host;
    return String(draft.raw) || specRow.default_host;
  };
  const help = readSettingHelp(row.key);
  const structured = row.kind === "json" ? parseJson(String(raw)) : undefined;
  return (
    <div className={`setting-row${drafts[row.key] ? " edited" : ""}`}>
      <div className="setting-text">
        <div className="setting-name">
          <span className="setting-label">{label ?? readSettingLabel(row.key)}</span>
          {/* only a change typed here and not saved yet is marked; a saved value carries no mark */}
          {drafts[row.key] && <span className="pill warn" title="typed here, not saved yet">unsaved</span>}
        </div>
        {(help || row.comment) && <p className="muted setting-comment">{help || renderComment(row.comment)}</p>}
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
            options={row.choices.map((choice) => ({ value: choice, label: readChoiceLabel(row.key, choice), hint: "", group: "" }))} />
        ) : row.kind === "long-text" ? (
          <textarea aria-label={row.key} value={String(raw ?? "")} rows={4}
            placeholder={describeRunValue(row)} onChange={(event) => edit(row, event.target.value)} />
        ) : row.kind === "json" ? (
          structured === undefined
            ? <p className="muted">This value cannot be shown as a table.</p>
            : <StructuredValueEditor settingKey={row.key} label={row.key} value={structured}
                onChange={(next) => edit(row, JSON.stringify(next, null, 2))} />
        ) : (
          <input type={row.kind === "number" ? "number" : "text"} aria-label={row.key} value={String(raw)}
            placeholder={row.value === null || row.value === "" ? describeRunValue(row) : undefined}
            onChange={(event) => edit(row, event.target.value)} />
        )}
      </div>
    </div>
  );
}
