import { useState } from "react";
import { buildRunPath, describeError, isSameName, nameRuns } from "../api";
import type { RunSummary } from "../api";
import { toggleInSet } from "../sets";
import Icon from "./Icon";

// The Manager tab's Rename, in the detail view: the runs that have an AI name (the topic phase 4 wrote), in two tabs
// by the name each is shown under. A run under its file name is renamed to its AI name; one under its AI name only
// back to the name it had: one list of both, with both buttons, read as runs still to rename (the owner: "its already
// AI names", then tabs inside).
const TABS = [
  { key: "file", title: "File name", isByTopic: true, action: "Use AI name", icon: "pencil" },
  { key: "topic", title: "AI name", isByTopic: false, action: "Use file name", icon: "restore" },
] as const;
type TabKey = (typeof TABS)[number]["key"];
// The server's outcome for a run still writing into its folder (`run_operations_service.RENAMED_WHEN_RUN_ENDS`)
const RENAMED_WHEN_RUN_ENDS = "renamed when its run ends";

export default function RenameView({ runs, onBack, onChanged, onMessage }: { runs: RunSummary[]; onBack: () => void;
  onChanged: () => void; onMessage: (text: string, isError?: boolean) => void }) {
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [isBusy, setIsBusy] = useState(false);
  const named = runs.filter((run) => run.topic);
  const isUnderTopic = (run: RunSummary) => Boolean(run.display_name) && isSameName(run.display_name, run.topic);
  const listOf = (key: TabKey) => named.filter((run) => isUnderTopic(run) === (key === "topic"));
  const [openTab, setOpenTab] = useState<TabKey>("file");
  const tab = TABS.find((candidate) => candidate.key === openTab) ?? TABS[0];
  const shownRuns = listOf(tab.key);
  const chosen = shownRuns.filter((run) => ticked.has(buildRunPath(run)));
  const toggle = (key: string) => setTicked((previous) => toggleInSet(previous, key));

  const apply = async () => {
    setIsBusy(true);
    try {
      const answer = await nameRuns(chosen.map((run) => ({ root: run.root, name: run.name })), tab.isByTopic);
      // the count, then each run kept with why (no AI name yet, the name taken), which the message keeps on screen
      // until it is closed; a run still writing is renamed when it ends, which is named and is no error
      const later = answer.records.filter((record) => record.outcome === RENAMED_WHEN_RUN_ENDS);
      const kept = answer.records.filter((record) => record.outcome !== "renamed" && record.outcome !== RENAMED_WHEN_RUN_ENDS);
      const renamedCount = answer.records.length - kept.length - later.length;
      onMessage([`${renamedCount} of ${answer.records.length} run${answer.records.length === 1 ? "" : "s"} renamed to their ${tab.isByTopic ? "AI name" : "file name"}`,
        ...later.map((record) => `${record.name}: ${record.outcome}`),
        ...kept.map((record) => `${record.name}: ${record.outcome}`)].join(" · "), kept.length > 0);
      setTicked(new Set());
      onChanged();
    } catch (problem) {
      onMessage(`Rename failed: ${describeError(problem)}`, true);
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <div className="detail manager-tool">
      <header className="detail-head">
        <button className="btn ghost icon-only back" onClick={onBack} aria-label="back"><Icon name="back" /></button>
        <div className="detail-title">
          <h2>Rename</h2>
          <small className="muted">Rename each ticked run folder to its AI name, or back to the name it had. A run that is writing keeps its name.</small>
        </div>
      </header>
      <nav className="tabs rename-tabs" role="tablist" aria-label="runs by the name they are shown under">
        {TABS.map((candidate) => (
          <button key={candidate.key} role="tab" aria-selected={candidate.key === tab.key}
            className={candidate.key === tab.key ? "on" : ""} onClick={() => { setOpenTab(candidate.key); setTicked(new Set()); }}>
            {candidate.title} ({listOf(candidate.key).length})
          </button>
        ))}
      </nav>
      {/* the action above the list, so it is in sight however many runs the list holds */}
      <div className="toolbar">
        <button className={`btn ${chosen.length ? "primary" : "muted-btn"}`} disabled={!chosen.length || isBusy}
          onClick={() => void apply()}>
          <Icon name={tab.icon} /> {chosen.length ? `${tab.action} for ${chosen.length}` : tab.action}
        </button>
        {/* in the toolbar, small and muted: under the tabs as a row of the list, it read as a run of its own (the owner) */}
        {shownRuns.length > 0 && (
          <label className="tick-all">
            <input type="checkbox" aria-label="tick every run shown" checked={chosen.length === shownRuns.length}
              onChange={(event) => setTicked(event.target.checked ? new Set(shownRuns.map(buildRunPath)) : new Set())} />
            Select all {shownRuns.length}
          </label>
        )}
      </div>
      {!shownRuns.length && <p className="muted">No run here.</p>}
      <ul className="manager-runs">
        {shownRuns.map((run) => {
          const shown = run.display_name || run.name;
          return (
            <li key={buildRunPath(run)}>
              <label className={`row selectable${ticked.has(buildRunPath(run)) ? " ticked" : ""}`}>
                <input type="checkbox" checked={ticked.has(buildRunPath(run))} onChange={() => toggle(buildRunPath(run))} />
                <span className="row-main">
                  <span className="row-name" title={run.name}>{shown}</span>
                  {!isSameName(run.topic, shown) && (
                    <small className="topic" title={run.topic}>{`AI name: ${run.topic}`}</small>
                  )}
                </span>
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
