import { describeError, formatDateTime, queueJobs, sendJson } from "../api";
import type { Job } from "../api";
import { REQUEUABLE_STATUSES, describeJob, describeJobStatus, describeRetry, isJobOpen, nameJob, readJobSelection,
  sendJobAction } from "../jobs";
import { usePersistentState } from "../viewState";
import Icon from "./Icon";

// The job queue, newest first: one `i2k run` at a time. A click shows what the job's process printed.
export default function QueuePanel({
  jobs,
  isLoaded = true,
  openJobId,
  onOpen,
  onChanged,
  onMessage,
}: {
  jobs: Job[];
  // whether the jobs were read yet: until then the queue says it is loading, never that nothing waits
  isLoaded?: boolean;
  openJobId: string;
  onOpen: (job: Job) => void;
  onChanged: () => void;
  onMessage: (text: string, isError?: boolean) => void;
}) {
  // One action on the queue: sent, its line shown when it has one, the lists read again; a refusal says why
  const runAction = async (send: () => Promise<string | void>) => {
    try {
      const line = await send();
      if (line) onMessage(line);
      onChanged();
    } catch (error) {
      onMessage(describeError(error), true);
    }
  };
  // An ended job queued again with every choice it was queued with
  const queueAgain = (job: Job) => runAction(async () => {
    const queued = await queueJobs([{ video: job.request.video, output: job.request.output_root }], readJobSelection(job.request));
    return `Queued again as job ${queued[0]?.job_id}`;
  });
  const move = (job: Job, direction: "up" | "down") => runAction(async () => {
    await sendJson(`/jobs/${job.job_id}/move`, { direction });
  });
  const clearEnded = () => runAction(async () => {
    const answer = await sendJson<{ cleared: number }>("/jobs/clear");
    return `${answer.cleared} ended job${answer.cleared === 1 ? "" : "s"} cleared`;
  });
  const cancel = (job: Job) => runAction(() => sendJobAction(job, "cancel"));

  // The queue holds the running job and what waits; an ended one moves to the finished list, where what its process
  // printed stays readable (a refused run line says why there).
  // A job waiting for its retry, or holding the queue, stands with the running one: it goes before every waiting job
  const running = jobs.filter((job) => job.status === "running" || job.status === "retrying" || job.status === "blocked");
  const waiting = jobs.filter((job) => job.status === "queued");
  const finished = [...jobs].reverse().filter((job) => !isJobOpen(job));
  // With jobs on more than one worker set, each set gets its own group: its running job, then its waiting jobs
  // numbered in the order that set runs them, since the sets run side by side. With one set, one plain list.
  const nameLane = (job: Job) => job.lane || job.request.worker_set || "default";
  const lanes = [...new Set([...running, ...waiting].map(nameLane))];
  // kept open or closed when the tab is left: with nothing waiting, it is all the tab shows
  const [isFinishedOpen, setIsFinishedOpen] = usePersistentState("finishedJobsOpen", false);

  const renderRow = (job: Job, position?: number, waitingCount = waiting.length) => {
    const { folder, recording } = nameJob(job);
    const retry = describeRetry(job);
    return (
    <li key={job.job_id} className="row-with-menu">
      <button className={`row${job.job_id === openJobId ? " selected" : ""}`} onClick={() => onOpen(job)}
        aria-current={job.job_id === openJobId || undefined} aria-description={describeJobStatus(job)}
        title={`${folder}\n${job.request.video.split("/").pop()}\n${describeJob(job)} · job ${job.job_id} · ${job.status}`}>
        {position !== undefined ? <span className="position">{position}</span> : <span className={`dot ${job.status}`} />}
        <span className="row-main">
          <span className="row-name">{folder}</span>
          <small className="muted">{recording ? `${recording} · ` : ""}{describeJob(job)} · {retry ? `${retry} · ` : ""}{formatDateTime(job.ended_at || job.queued_at)}</small>
        </span>
        {position === undefined && (
          <span className={`pill ${job.status}`} title={describeJobStatus(job)}>
            {job.status}
          </span>
        )}
      </button>
      {job.status === "queued" && position !== undefined && (
        <>
          <button className="btn ghost icon-only" title="run it before the job above" aria-label="run sooner"
            disabled={position === 1} onClick={() => void move(job, "up")}>
            <span className="turn-up"><Icon name="chevron" size={14} /></span>
          </button>
          <button className="btn ghost icon-only" title="run it after the job below" aria-label="run later"
            disabled={position === waitingCount} onClick={() => void move(job, "down")}>
            <Icon name="chevron" size={14} />
          </button>
        </>
      )}
      {(job.status === "queued" || job.status === "retrying") && (
        <button className="btn ghost icon-only" title="take it off the queue" aria-label="cancel"
          onClick={() => void cancel(job)}>
          <Icon name="x" />
        </button>
      )}
      {job.status === "blocked" && (
        <>
          <button className="btn ghost icon-only" title="run it once more, before any other job" aria-label="retry"
            onClick={() => void runAction(() => sendJobAction(job, "retry"))}>
            <Icon name="refresh" />
          </button>
          <button className="btn ghost icon-only" title="let it go as failed, so the next job starts" aria-label="skip"
            onClick={() => void runAction(() => sendJobAction(job, "skip"))}>
            <Icon name="play" />
          </button>
        </>
      )}
      {REQUEUABLE_STATUSES.includes(job.status) && (
        <button className="btn ghost icon-only" title="queue it again with the same choices" aria-label="queue again"
          onClick={() => void queueAgain(job)}>
          <Icon name="refresh" />
        </button>
      )}
    </li>
    );
  };

  return (
    <div className="tab-body">
      {lanes.length > 1 ? lanes.map((lane) => {
        const laneRunning = running.filter((job) => nameLane(job) === lane);
        const laneWaiting = waiting.filter((job) => nameLane(job) === lane);
        return (
          <section key={lane} className="queue-lane" aria-label={`worker set ${lane}`}>
            <h3 className="menu-heading">
              Worker set {lane} · {laneRunning.length} running · {laneWaiting.length} waiting
            </h3>
            <ul className="rows">
              {laneRunning.map((job) => renderRow(job))}
              {laneWaiting.map((job, index) => renderRow(job, index + 1, laneWaiting.length))}
            </ul>
          </section>
        );
      }) : (
        <ul className="rows">
          {running.map((job) => renderRow(job))}
          {!isLoaded && <li className="empty-row" role="status">Loading the queue…</li>}
          {isLoaded && waiting.length === 0 && (
            <li className="empty-row">Nothing is waiting. A job runs as soon as nothing else runs.</li>
          )}
          {waiting.map((job, index) => renderRow(job, index + 1))}
        </ul>
      )}
      {finished.length > 0 && (
        <div className="inbox-section">
          <div className="section-head">
            <span className="section-icon"><Icon name="list" size={14} /></span>
            <button className="section-toggle" onClick={() => setIsFinishedOpen(!isFinishedOpen)} aria-expanded={isFinishedOpen}>
              <span>Finished jobs</span>
              <span className="count">{finished.length}</span>
              <span className={`caret${isFinishedOpen ? " open" : ""}`}><Icon name="chevron" size={14} /></span>
            </button>
          </div>
          {isFinishedOpen && (
            <>
              <button className="btn small" onClick={() => void clearEnded()}><Icon name="trash" size={14} /> Clear ended jobs</button>
              <ul className="rows">{finished.map((job) => renderRow(job))}</ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}
