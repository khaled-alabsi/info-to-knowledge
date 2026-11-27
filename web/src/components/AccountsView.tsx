import { useEffect, useState } from "react";
import { addAccount, listAccounts, removeAccount, updateAccount } from "../api";
import type { AccountRow } from "../api";
import Icon from "./Icon";

const ROLES = ["viewer", "operator", "admin"];
const ROLE_HINTS: Record<string, string> = {
  viewer: "reads runs, documents, logs, the inbox and the queue",
  operator: "also starts, stops and resets runs, uploads, edits and tags",
  admin: "also changes the settings, the setup and the accounts",
};

// The Admin tab's Users, for an admin: every account with its role, a new account, a
// role changed, a password set again, an account removed. The server refuses what it cannot do, in its own words.
export default function AccountsView({ onBack, onMessage }: { onBack: () => void;
  onMessage: (text: string, isError?: boolean) => void }) {
  const [accounts, setAccounts] = useState<AccountRow[]>([]);
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState("viewer");

  useEffect(() => {
    listAccounts().then(setAccounts).catch((problem) => onMessage(`Accounts not read: ${(problem as Error).message}`, true));
  }, [onMessage]);

  const apply = async (action: () => Promise<AccountRow[]>, done: string) => {
    try {
      setAccounts(await action());
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
          <h2>Users</h2>
          <small className="muted">Who may sign in, and what each role may do. With no account, the server asks nobody to sign in.</small>
        </div>
      </header>
      <form className="toolbar account-new" onSubmit={async (event) => {
        event.preventDefault();
        if (await apply(() => addAccount(name, password, role), `Account ${name} added as ${role}`)) {
          setName("");
          setPassword("");
        }
      }}>
        <input aria-label="new account's name" placeholder="name" value={name} onChange={(event) => setName(event.target.value)} />
        <input aria-label="new account's password" type="password" placeholder="password" value={password}
          onChange={(event) => setPassword(event.target.value)} />
        <select aria-label="new account's role" value={role} onChange={(event) => setRole(event.target.value)}>
          {ROLES.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
        <button className="btn primary" type="submit" disabled={!name || !password}><Icon name="check" /> Add account</button>
      </form>
      <ul className="manager-runs">
        {accounts.map((account) => (
          <li key={account.name} className="row account-row">
            <span className="row-main">
              <span className="row-name">{account.name}</span>
              <small className="muted">{ROLE_HINTS[account.role] ?? account.role}</small>
            </span>
            <select aria-label={`${account.name}'s role`} value={account.role}
              onChange={(event) => void apply(() => updateAccount(account.name, { role: event.target.value }),
                `${account.name} is ${event.target.value} now`)}>
              {ROLES.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
            <button className="btn ghost" title="set a new password; the account's sessions end" onClick={() => {
              const next = window.prompt(`New password for ${account.name}`);
              if (next) void apply(() => updateAccount(account.name, { password: next }), `${account.name} has a new password`);
            }}><Icon name="lock" /> Password</button>
            <button className="btn danger" onClick={() => {
              if (window.confirm(`Remove the account ${account.name}? Its sessions end.`)) {
                void apply(() => removeAccount(account.name), `Account ${account.name} removed`);
              }
            }}><Icon name="trash" /> Remove</button>
          </li>
        ))}
        {accounts.length === 0 && <li className="muted">No account yet: the server asks nobody to sign in.</li>}
      </ul>
    </div>
  );
}
