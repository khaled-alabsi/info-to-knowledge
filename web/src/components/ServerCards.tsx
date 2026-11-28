import { useState } from "react";
import type { SettingRow } from "../api";
import { asTree, isTree, listModelNames, readModelProvider, readPath } from "../modelLibrary";
import type { Tree } from "../modelLibrary";
import { describeHost } from "../settingRunValue";
import { PROVIDER_JOB_CHOICES } from "../settingsLabels";
import Icon from "./Icon";
import ModelLibrary from "./ModelLibrary";
import type { SettingEditContext } from "./SettingRowItem";

// The model servers, one card each: its name, its address, the name of the variable holding its access token and the jobs
// it can do, with the models that run on it inside the card. This is the only place a model is set. A model that runs on no
// listed server (OpenAI's own service, or one named after a server the settings no longer list) stands in a last card of its
// own. The servers are the `hosts` setting and the models the `models` settings; the page only groups the second under the
// first.

export const HOSTS_KEY = "hosts";

const JOB_CHOICES = PROVIDER_JOB_CHOICES;

function readHosts(rows: SettingRow[], context: SettingEditContext): Tree {
  const hostsRow = rows.find((row) => row.key === HOSTS_KEY);
  const draft = context.drafts[HOSTS_KEY];
  if (draft) {
    try {
      const parsed: unknown = JSON.parse(String(draft.raw));
      if (isTree(parsed)) return parsed;
    } catch {
      // the page writes this draft itself, so an unreadable one leaves the saved value
    }
  }
  return asTree(hostsRow?.value);
}

// The server a model not saved yet runs on: its own `host`, else the server named like the start of its spec
function readNewModelServer(row: SettingRow, context: SettingEditContext, hosts: Tree): string {
  let fields: Tree = {};
  try {
    fields = asTree(JSON.parse(String(context.drafts[row.key]?.raw ?? "{}")));
  } catch {
    fields = {};
  }
  if (typeof fields.host === "string" && fields.host in hosts) return fields.host;
  const start = String(fields.spec ?? "").split(":")[0];
  return start in hosts ? start : "";
}

// How many cards the section shows: one a server, and one more for the models no listed server runs
export function countServerCards(rows: SettingRow[], context: SettingEditContext, newRows: SettingRow[]): number {
  const hosts = readHosts(rows, context);
  const unplaced = listModelNames(rows).filter((name) => readModelProvider(rows, name) === "").length
    + newRows.filter((row) => readNewModelServer(row, context, hosts) === "").length;
  return Object.keys(hosts).length + (unplaced > 0 ? 1 : 0);
}

export default function ServerCards({ rows, context, newRows, onAdd, onDiscard, onEditNew, onRemove }: {
  rows: SettingRow[];
  context: SettingEditContext;
  // the rows of the models added and not saved yet
  newRows: SettingRow[];
  onAdd: (name: string, source: string, host: string) => void;
  onDiscard: (name: string) => void;
  onEditNew: (name: string, field: string, value: unknown) => void;
  onRemove: (name: string) => void;
}) {
  const [newServer, setNewServer] = useState<string | null>(null);
  const [typedName, setTypedName] = useState("");
  const hostsRow = rows.find((row) => row.key === HOSTS_KEY);
  const hosts = readHosts(rows, context);
  const serverNames = Object.keys(hosts);
  const savedModels = listModelNames(rows);
  const takenModels = [...savedModels, ...newRows.map((row) => readPath(row)[1])];
  const writeHosts = (next: Tree) => hostsRow && context.edit(hostsRow, JSON.stringify(next, null, 2));
  const setField = (server: string, field: string, value: unknown) => {
    const { [field]: _old, ...others } = asTree(hosts[server]);
    writeHosts({ ...hosts, [server]: value === "" || (Array.isArray(value) && value.length === 0) ? others : { ...others, [field]: value } });
  };
  const nameTaken = serverNames.includes(typedName.trim());
  const addServer = () => {
    const name = typedName.trim();
    if (!name || nameTaken) return;
    writeHosts({ ...hosts, [name]: { url: "", kinds: ["chat"] } });
    setTypedName("");
    setNewServer(null);
  };

  const modelsOf = (server: string) => savedModels.filter((name) => readModelProvider(rows, name) === server);
  const newModelsOf = (server: string) => newRows.filter((row) => readNewModelServer(row, context, hosts) === server);
  const unplaced = modelsOf("").length + newModelsOf("").length;
  const renderModels = (server: string) => (
    <ModelLibrary rows={rows} context={context} host={server} names={modelsOf(server)} newRows={newModelsOf(server)}
      takenNames={takenModels} onAdd={onAdd} onDiscard={onDiscard} onEditNew={onEditNew} onRemove={onRemove} />
  );

  return (
    <div className="server-cards">
      {serverNames.map((server) => {
        const entry = asTree(hosts[server]);
        const kinds = Array.isArray(entry.kinds) ? entry.kinds.map(String) : ["chat"];
        const isEdited = Boolean(context.drafts[HOSTS_KEY]);
        return (
          <div key={server} className={`server-card${isEdited ? " edited" : ""}`} role="group" aria-label={`server ${server}`}>
            <div className="server-card-head">
              <h4>{describeHost(server)}</h4>
              <button type="button" className="btn danger" aria-label={`remove server ${server}`}
                onClick={() => writeHosts(Object.fromEntries(Object.entries(hosts).filter(([name]) => name !== server)))}>
                <Icon name="trash" size={14} /> Remove</button>
            </div>
            <div className="server-fields">
              <label className="server-field">
                <span className="setting-label">Address</span>
                <input type="text" aria-label={`address of ${server}`} value={String(entry.url ?? "")}
                  placeholder="http://computer.local:1234/v1" onChange={(event) => setField(server, "url", event.target.value)} />
              </label>
              <label className="server-field">
                <span className="setting-label">Token variable</span>
                <input type="text" aria-label={`token variable of ${server}`} value={String(entry.apiKeyEnv ?? "")}
                  placeholder="empty: no login, or the variable's name as OPENROUTER_API_KEY"
                  onChange={(event) => setField(server, "apiKeyEnv", event.target.value)} />
              </label>
              <div className="server-field">
                <span className="setting-label">Jobs it can do</span>
                <div className="suits">
                  {JOB_CHOICES.map((choice) => (
                    <label key={choice.value} className="suit-choice">
                      <input type="checkbox" aria-label={`${choice.label}, ${server}`} checked={kinds.includes(choice.value)}
                        onChange={(event) => setField(server, "kinds", JOB_CHOICES.map((each) => each.value).filter((value) =>
                          value === choice.value ? event.target.checked : kinds.includes(value)))} />
                      {choice.label}
                    </label>
                  ))}
                </div>
              </div>
            </div>
            <h5 className="server-models-head">Models on {describeHost(server)}</h5>
            {renderModels(server)}
          </div>
        );
      })}
      {unplaced > 0 && (
        <div className="server-card" role="group" aria-label="models on no listed server">
          <div className="server-card-head"><h4>No listed server</h4></div>
          <p className="muted">These models run on a service the list above does not hold, such as OpenAI's own. Pick a server in a model's card to move it.</p>
          {renderModels("")}
        </div>
      )}
      {!hostsRow ? null : newServer === null ? (
        <div className="setting-row add-model">
          <button type="button" className="btn" onClick={() => setNewServer("")}>+ Add server</button>
        </div>
      ) : (
        <div className="setting-row edited add-model">
          <input type="text" aria-label="New server's name" placeholder="name, as gpu-box or laptop" autoFocus value={typedName}
            onChange={(event) => setTypedName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") addServer(); }} />
          <button type="button" className="btn primary" onClick={addServer} disabled={!typedName.trim() || nameTaken}>Add</button>
          <button type="button" className="btn" onClick={() => { setNewServer(null); setTypedName(""); }}>Cancel</button>
          {nameTaken && <small className="error">a server has this name</small>}
        </div>
      )}
    </div>
  );
}
