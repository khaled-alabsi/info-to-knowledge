import { useEffect, useState } from "react";
import { describeError, fetchJson } from "../api";
import type { Job, JobOnFailure, SettingRow } from "../api";
import { describeRetry, sendJobAction, setJobOnFailure } from "../jobs";
import { Menu } from "./Dropdown";
import type { MenuItem } from "./Dropdown";
import Icon from "./Icon";

type Report = (text: string, isError?: boolean) => void;

const OPEN_STATES = ["queued", "running", "retrying", "blocked"];

// The job's own choice for after its last failed try, as an on/off item of a menu (the owner: the switch "look like
// shit", "merge it to the menu Control"): on, the queue holds until the person retries or skips the job; off, the next
// job starts. A job with no choice of its own stands where `queue.onFailure` puts it. Null for no job, or one that ended.
export function useHoldQueueItem(job: Job | null, onChanged: () => void, onMessage: Report): MenuItem | null {
  const [settingChoice, setSettingChoice] = useState<string>("continue");
  useEffect(() => {
    let isOpen = true;
    fetchJson<SettingRow[]>("/settings")
      .then((rows) => {
        const row = rows.find((candidate) => candidate.key === "queue.onFailure");
        if (isOpen && typeof row?.value === "string") setSettingChoice(row.value);
      })
      .catch(() => undefined);
    return () => { isOpen = false; };
  }, []);
  if (!job || !OPEN_STATES.includes(job.status)) return null;
  const isHolding = (job.request.on_failure || settingChoice) === "block";
  return {
    label: "Hold queue on failure",
    hint: isHolding ? "the queue waits for you" : "the next job starts",
    isChecked: isHolding,
    onSelect: () => {
      setJobOnFailure(job, (isHolding ? "continue" : "block") as JobOnFailure)
        .then((text) => { onMessage(text); onChanged(); })
        .catch((error) => onMessage(describeError(error), true));
    },
  };
}

// What a failed job shows in a toolbar of the detail view: which retry it is on, and Retry and Skip while it holds the
// queue. `holdItem`, when given, is drawn as a menu of its own; a view with a Control menu puts it there instead.
export default function JobFailureControls({ job, onChanged, onMessage, holdItem }: {
  job: Job;
  onChanged: () => void;
  onMessage: Report;
  holdItem?: MenuItem | null;
}) {
  const act = async (send: () => Promise<string>) => {
    try {
      onMessage(await send());
      onChanged();
    } catch (error) {
      onMessage(describeError(error), true);
    }
  };
  if (!OPEN_STATES.includes(job.status)) return null;
  const retry = describeRetry(job);

  return (
    <>
      {retry && (
        <span className="state-note" title="a failed job runs again with --resume before any other job">
          <span className={`dot ${job.status}`} /> {job.status === "blocked" ? `${retry}: the queue holds` : retry}
        </span>
      )}
      {job.status === "blocked" && (
        <>
          <button className="btn primary" onClick={() => void act(() => sendJobAction(job, "retry"))}
            title="run it once more, with --resume, before any other job">
            <Icon name="refresh" /> Retry
          </button>
          <button className="btn" onClick={() => void act(() => sendJobAction(job, "skip"))}
            title="let it go as failed, so the next job starts">
            <Icon name="play" /> Skip
          </button>
        </>
      )}
      {holdItem && <Menu label="On failure" icon="list" items={[holdItem]} />}
    </>
  );
}
