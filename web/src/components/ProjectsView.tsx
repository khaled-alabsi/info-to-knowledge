import { useEffect, useState } from "react";
import { addProject, fetchJson, fetchProjects, listAccounts, moveRunToProject, removeProject, renameProject,
  setProjectGroup, setProjectMember } from "../api";
import type { AccountRow, Project, ProjectsState, RunSummary } from "../api";
import Icon from "./Icon";

const ROLES = ["viewer", "operator", "admin"];
const DEFAULT_PROJECT = "default";

// One grant of a project: to an account or to a group, with its role
interface Grant {
  kind: "account" | "group";
  name: string;
  role: string;
}

// The Admin tab's Projects: each project with the roles it gives to accounts and groups and
// the runs moved into it. Every run is in the default project until moved; there every account has its own role
// unless a grant gives it another, and another project is open only to those it gives a role.
export default function ProjectsView({ onBack, onMessage }: { onBack: () => void;
  onMessage: (text: string, isError?: boolean) => void }) {
  const [state, setState] = useState<ProjectsState>({ projects: [], groups: [] });
  const [accounts, setAccounts] = useState<AccountRow[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [title, setTitle] = useState("");

  useEffect(() => {
    fetchProjects().then(setState).catch((problem) => onMessage(`Projects not read: ${(problem as Error).message}`, true));
    listAccounts().then(setAccounts).catch(() => setAccounts([]));
    fetchJson<RunSummary[]>("/runs").then(setRuns).catch(() => setRuns([]));
  }, [onMessage]);

  const apply = async (action: () => Promise<ProjectsState>, done: string) => {
    try {
      setState(await action());
      setRuns(await fetchJson<RunSummary[]>("/runs"));
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
          <h2>Projects</h2>
          <small className="muted">Who sees which runs. Every run is in Default until moved; another project is open only to
            the accounts and groups it gives a role.</small>
        </div>
      </header>
      <form className="toolbar account-new" onSubmit={async (event) => {
        event.preventDefault();
        if (await apply(() => addProject(title), `Project ${title} added`)) setTitle("");
      }}>
        <input aria-label="new project's title" placeholder="project title" value={title} onChange={(event) => setTitle(event.target.value)} />
        <button className="btn primary" type="submit" disabled={!title.trim()}><Icon name="check" /> Add project</button>
      </form>
      {state.projects.map((project) => (
        <ProjectCard key={project.slug} project={project} accounts={accounts} groups={state.groups.map((group) => group.name)}
          runs={runs} onApply={apply} />
      ))}
    </div>
  );
}

function ProjectCard({ project, accounts, groups, runs, onApply }: { project: Project; accounts: AccountRow[];
  groups: string[]; runs: RunSummary[]; onApply: (action: () => Promise<ProjectsState>, done: string) => Promise<boolean> }) {
  const grants: Grant[] = [
    ...Object.entries(project.members ?? {}).map(([name, role]) => ({ kind: "account" as const, name, role })),
    ...Object.entries(project.groups ?? {}).map(([name, role]) => ({ kind: "group" as const, name, role })),
  ];
  const setGrant = (grant: Grant, role: string | null) => grant.kind === "account"
    ? setProjectMember(project.slug, grant.name, role) : setProjectGroup(project.slug, grant.name, role);
  const ungranted: Grant[] = [
    ...accounts.filter((account) => !(account.name in (project.members ?? {})))
      .map((account) => ({ kind: "account" as const, name: account.name, role: "viewer" })),
    ...groups.filter((group) => !(group in (project.groups ?? {}))).map((name) => ({ kind: "group" as const, name, role: "viewer" })),
  ];
  const own = runs.filter((run) => (run.project ?? DEFAULT_PROJECT) === project.slug);
  const elsewhere = runs.filter((run) => (run.project ?? DEFAULT_PROJECT) !== project.slug);
  const isDefault = project.slug === DEFAULT_PROJECT;

  return (
    <section className="card project-card" aria-label={`project ${project.title}`}>
      <header className="project-head">
        <h3>{project.title}</h3>
        <small className="muted">{isDefault ? "every run no other project holds" : `${own.length} run(s)`}</small>
        <span className="spacer" />
        <button className="btn ghost" onClick={() => {
          const next = window.prompt(`New title for ${project.title}`, project.title);
          if (next && next.trim()) void onApply(() => renameProject(project.slug, next), `Project renamed to ${next}`);
        }}><Icon name="pencil" /> Rename</button>
        {!isDefault && (
          <button className="btn danger" onClick={() => {
            if (window.confirm(`Remove the project ${project.title}? Its runs go back to Default.`)) {
              void onApply(() => removeProject(project.slug), `Project ${project.title} removed`);
            }
          }}><Icon name="trash" /> Remove</button>
        )}
      </header>
      <h4 className="project-part">Roles {isDefault && <small className="muted">(every account also has its own role here)</small>}</h4>
      <ul className="manager-runs">
        {grants.map((grant) => (
          <li key={`${grant.kind}:${grant.name}`} className="row account-row">
            <span className="row-main"><span className="row-name">{grant.name}</span>
              <small className="muted">{grant.kind === "group" ? "group" : "account"}</small></span>
            <select aria-label={`${grant.name}'s role in ${project.title}`} value={grant.role}
              onChange={(event) => void onApply(() => setGrant(grant, event.target.value), `${grant.name} is ${event.target.value} in ${project.title}`)}>
              {ROLES.map((role) => <option key={role} value={role}>{role}</option>)}
            </select>
            <button className="btn ghost" aria-label={`take ${grant.name}'s role in ${project.title} away`}
              onClick={() => void onApply(() => setGrant(grant, null), `${grant.name} has no role of its own in ${project.title} now`)}>
              <Icon name="x" /> Remove</button>
          </li>
        ))}
        {grants.length === 0 && <li className="muted">{isDefault ? "No role given here beyond each account's own." : "Nobody has a role here yet: only admins see it."}</li>}
      </ul>
      {ungranted.length > 0 && (
        <select aria-label={`give a role in ${project.title}`} value="" onChange={(event) => {
          const grant = ungranted.find((item) => `${item.kind}:${item.name}` === event.target.value);
          if (grant) void onApply(() => setGrant(grant, "viewer"), `${grant.name} is viewer in ${project.title}`);
        }}>
          <option value="" disabled>Give a role to…</option>
          {ungranted.map((grant) => <option key={`${grant.kind}:${grant.name}`} value={`${grant.kind}:${grant.name}`}>
            {grant.kind === "group" ? `group ${grant.name}` : grant.name}</option>)}
        </select>
      )}
      {!isDefault && (
        <>
          <h4 className="project-part">Runs</h4>
          <ul className="manager-runs">
            {own.map((run) => (
              <li key={run.name} className="row account-row">
                <span className="row-main"><span className="row-name">{run.display_name || run.name}</span></span>
                <button className="btn ghost" onClick={() => void onApply(() => moveRunToProject(DEFAULT_PROJECT, run.name),
                  `${run.name} moved back to Default`)}><Icon name="x" /> Move to Default</button>
              </li>
            ))}
          </ul>
          {elsewhere.length > 0 && (
            <select aria-label={`move a run into ${project.title}`} value="" onChange={(event) =>
              void onApply(() => moveRunToProject(project.slug, event.target.value), `${event.target.value} moved into ${project.title}`)}>
              <option value="" disabled>Move a run here…</option>
              {elsewhere.map((run) => <option key={run.name} value={run.name}>{run.display_name || run.name}</option>)}
            </select>
          )}
        </>
      )}
    </section>
  );
}
