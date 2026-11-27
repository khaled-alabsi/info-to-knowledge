import { useEffect, useState } from "react";
import { addGroup, fetchProjects, listAccounts, removeGroup, setGroupAccounts } from "../api";
import type { AccountRow, Group, ProjectsState } from "../api";
import Icon from "./Icon";

// The Admin tab's Groups: each group with the accounts in it. A project gives a role to a
// group, and every account in it has that role there; with a single sign-on, the groups come from its token instead.
export default function GroupsView({ onBack, onMessage }: { onBack: () => void;
  onMessage: (text: string, isError?: boolean) => void }) {
  const [groups, setGroups] = useState<Group[]>([]);
  const [accounts, setAccounts] = useState<AccountRow[]>([]);
  const [name, setName] = useState("");

  useEffect(() => {
    fetchProjects().then((state) => setGroups(state.groups))
      .catch((problem) => onMessage(`Groups not read: ${(problem as Error).message}`, true));
    listAccounts().then(setAccounts).catch(() => setAccounts([]));
  }, [onMessage]);

  const apply = async (action: () => Promise<ProjectsState>, done: string) => {
    try {
      setGroups((await action()).groups);
      onMessage(done);
      return true;
    } catch (problem) {
      onMessage((problem as Error).message.replace(/^\d+: /, ""), true);
      return false;
    }
  };

  return (
    <div className="detail manager-tool">
      <header className="detail-head">
        <button className="btn ghost icon-only back" onClick={onBack} aria-label="back"><Icon name="back" /></button>
        <div className="detail-title">
          <h2>Groups</h2>
          <small className="muted">Accounts grouped, so a project gives one role to all of them.</small>
        </div>
      </header>
      <form className="toolbar account-new" onSubmit={async (event) => {
        event.preventDefault();
        if (await apply(() => addGroup(name), `Group ${name} added`)) setName("");
      }}>
        <input aria-label="new group's name" placeholder="group name" value={name} onChange={(event) => setName(event.target.value)} />
        <button className="btn primary" type="submit" disabled={!name.trim()}><Icon name="check" /> Add group</button>
      </form>
      <ul className="manager-runs">
        {groups.map((group) => {
          const others = accounts.filter((account) => !group.accounts.includes(account.name));
          return (
            <li key={group.name} className="row group-row">
              <span className="row-main">
                <span className="row-name">{group.name}</span>
                <span className="chips" aria-label={`${group.name}'s accounts`}>
                  {group.accounts.map((account) => (
                    <span key={account} className="chip">
                      {account}
                      <button className="chip-remove" aria-label={`take ${account} out of ${group.name}`}
                        onClick={() => void apply(() => setGroupAccounts(group.name, group.accounts.filter((item) => item !== account)),
                          `${account} left ${group.name}`)}><Icon name="x" size={12} /></button>
                    </span>
                  ))}
                  {group.accounts.length === 0 && <small className="muted">no account yet</small>}
                </span>
              </span>
              {others.length > 0 && (
                <select aria-label={`add an account to ${group.name}`} value=""
                  onChange={(event) => void apply(() => setGroupAccounts(group.name, [...group.accounts, event.target.value]),
                    `${event.target.value} joined ${group.name}`)}>
                  <option value="" disabled>Add account…</option>
                  {others.map((account) => <option key={account.name} value={account.name}>{account.name}</option>)}
                </select>
              )}
              <button className="btn danger" onClick={() => {
                if (window.confirm(`Remove the group ${group.name}? The projects' roles given to it end.`)) {
                  void apply(() => removeGroup(group.name), `Group ${group.name} removed`);
                }
              }}><Icon name="trash" /> Remove</button>
            </li>
          );
        })}
        {groups.length === 0 && <li className="muted">No group yet.</li>}
      </ul>
    </div>
  );
}
