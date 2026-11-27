import { useEffect, useState } from "react";
import { describeError, fetchJson } from "../api";
import type { WorkerActivity } from "../api";
import WorkerTurns, { formatClock } from "./WorkerTurns";

// How often the turns of a running worker are read again
const REFRESH_MS = 3000;

// One worker's session: a phase's worker, or one phase 7 part, with where its turns are read
export interface WorkerSession {
  key: string;
  label: string;
  readPath: string;
  isRunning: boolean;
}

// The Artifacts tab's Worker turns (the owner: "the drop down should select the session for each worker that already run
// or curently running, the defulty the currently running"): one session chosen in the list, its turns and tool calls
// shown as the worker's live panel shows them, read again while it runs. `chosenKey` opens a session another card named
export default function WorkerSessions({ sessions, chosenKey = "" }: { sessions: WorkerSession[]; chosenKey?: string }) {
  const running = sessions.find((session) => session.isRunning);
  const [key, setKey] = useState(chosenKey || running?.key || sessions[sessions.length - 1]?.key || "");
  const [activity, setActivity] = useState<WorkerActivity | null>(null);
  const [error, setError] = useState("");
  const session = sessions.find((candidate) => candidate.key === key) ?? running ?? sessions[sessions.length - 1];
  const index = session ? sessions.indexOf(session) : -1;

  useEffect(() => {
    if (chosenKey) setKey(chosenKey);
  }, [chosenKey]);

  useEffect(() => {
    if (!session) return;
    let isOpen = true;
    setActivity(null);
    const read = () => fetchJson<WorkerActivity>(session.readPath)
      .then((answer) => { if (isOpen) { setActivity(answer); setError(""); } })
      .catch((reason) => { if (isOpen) setError(describeError(reason)); });
    read();
    const timer = session.isRunning ? window.setInterval(read, REFRESH_MS) : undefined;
    return () => { isOpen = false; if (timer) window.clearInterval(timer); };
  }, [session?.readPath, session?.isRunning]);

  if (!session) return <p className="muted">No worker has run yet.</p>;
  const isWorking = session.isRunning && activity?.is_worker_running !== false;
  return (
    // the live panel's own look (`part-live`), so a turn reads the same in both
    <div className="worker-sessions part-live">
      {sessions.length > 1 && (
        <div className="file-picker">
          <button className="btn small" aria-label="previous session" title="previous session" disabled={index <= 0}
            onClick={() => setKey(sessions[index - 1].key)}>‹ Previous</button>
          <select className="file-select" aria-label="worker session" value={session.key}
            onChange={(event) => setKey(event.target.value)}>
            {sessions.map((candidate) => (
              <option key={candidate.key} value={candidate.key}>
                {candidate.label}{candidate.isRunning ? " · running" : ""}
              </option>
            ))}
          </select>
          <button className="btn small" aria-label="next session" title="next session"
            disabled={index >= sessions.length - 1} onClick={() => setKey(sessions[index + 1].key)}>Next ›</button>
        </div>
      )}
      {error && <p className="error">{error}</p>}
      {isWorking && activity?.doing?.what && (
        <p className="part-doing"><strong>Now: {activity.doing.what}</strong>
          {activity.doing.since ? <span className="muted"> · since {formatClock(activity.doing.since)}</span> : ""}</p>
      )}
      {activity && <WorkerTurns activity={activity} isRunning={session.isRunning} label={session.label} />}
    </div>
  );
}
