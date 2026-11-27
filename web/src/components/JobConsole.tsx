import type { Job } from "../api";
import { describeError } from "../api";
import { describeJob, describeJobStatus, nameJob, sendJobAction } from "../jobs";
import { useUpdateStream } from "../useUpdateStream";
import Icon from "./Icon";
import JobFailureControls, { useHoldQueueItem } from "./JobFailureControls";
import LogView from "./LogView";

interface JobUpdate {
  job: Job | null;
  offset: number;
}

// Everything one job's `i2k run` process printed, live. A refused run line says why here, before any log exists.
// `onOpenRun` opens the run the job writes (left out when the run list does not hold it yet); the job is taken off the
// queue while it waits, or interrupted while it runs
export default function JobConsole({ job, onBack, onOpenRun, onChanged = () => undefined, onMessage = () => undefined }: {
  job: Job;
  onBack: () => void;
  onOpenRun?: () => void;
  onChanged?: () => void;
  onMessage?: (text: string, isError?: boolean) => void;
}) {
  const { latest, text } = useUpdateStream<JobUpdate>(`/jobs/${job.job_id}/update`);
  const current = latest?.job ?? job;
  const holdItem = useHoldQueueItem(current, onChanged, onMessage);
  const act = async (action: "cancel" | "interrupt") => {
    try {
      onMessage(await sendJobAction(current, action));
      onChanged();
    } catch (error) {
      onMessage(describeError(error), true);
    }
  };

  return (
    <div className="detail">
      <header className="detail-head">
        <button className="btn ghost icon-only back" onClick={onBack} aria-label="back"><Icon name="back" /></button>
        <div className="detail-title">
          {/* named as its row in the Queue names it: the run folder, the recording under it */}
          <h2 className="truncate" title={current.request.video}>{nameJob(current).folder}</h2>
          <small className="muted">{nameJob(current).recording ? `${nameJob(current).recording} · ` : ""}job {current.job_id} · {describeJob(current)}</small>
        </div>
        <div className="head-pills">
          <span className={`pill ${current.status}`} title={describeJobStatus(current)}>
            {current.status}
          </span>
        </div>
      </header>
      <div className="toolbar">
        {onOpenRun && <button className="btn" onClick={onOpenRun}><Icon name="layers" /> Open run</button>}
        {(current.status === "queued" || current.status === "retrying") && (
          <button className="btn" onClick={() => void act("cancel")}><Icon name="x" /> Take off the queue</button>
        )}
        {current.status === "running" && (
          <button className="btn danger" onClick={() => void act("interrupt")}><Icon name="stop" /> Interrupt</button>
        )}
        <JobFailureControls job={current} onChanged={onChanged} onMessage={onMessage} holdItem={holdItem} />
      </div>
      <code className="command">{current.command.slice(1).join(" ")}</code>
      <LogView title="Job console" text={text} empty="The job has printed nothing yet." />
    </div>
  );
}
