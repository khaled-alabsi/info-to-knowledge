import { useEffect, useState } from "react";
import { describeError, fetchJson, formatDateTime, formatDuration } from "../api";
import type { BatchHistoryEntry } from "../api";
import Icon from "./Icon";
import { formatMarkdownCell } from "../markdownTable";
import CopyButton from "./CopyButton";
import Spinner from "./Spinner";

// The table under the Frames card: every phase 3 batch and follow-up with its time, tokens and result, and each refused
// attempt with why. Read from the run's logs, so a server restart or a resume loses none of it.
// The newest batch first, each batch's steps in the order they ran (reading, critique, fix, open questions, answers),
// so a batch reads top-down as its flow and the newest work is on top
function orderNewestBatchFirst(batches: BatchHistoryEntry[]) {
  const groups: BatchHistoryEntry[][] = [];
  for (const batch of batches) {
    const last = groups[groups.length - 1];
    if (last && last[0].frames === batch.frames) last.push(batch);
    else groups.push([batch]);
  }
  return groups.reverse().flat();
}

// Each row with the number of its batch (the frames it covers) and whether it opens it, so a batch's steps are drawn as
// one block: its frames named once, a line above it and every other batch shaded
function groupRows(batches: BatchHistoryEntry[]) {
  let group = -1;
  return batches.map((batch, index) => {
    const isFirst = index === 0 || batches[index - 1].frames !== batch.frames;
    if (isFirst) group += 1;
    return { batch, group, isFirst };
  });
}

// The table as markdown, one row per step, its rejections in the last cell, so it pastes whole into a chat or a note
function formatTableAsMarkdown(batches: BatchHistoryEntry[], rejectionCount: number, unitTitle: string) {
  const lines = [`| ${unitTitle} | Step | Took | Tokens in / out | Result | Rejections (${rejectionCount}) |`,
    "| --- | --- | --- | --- | --- | --- |"];
  for (const { batch, isFirst } of groupRows(orderNewestBatchFirst(batches))) {
    const rejections = batch.rejections.map((rejection) => `attempt ${rejection.attempt}: `
      + rejection.findings.map((finding) => `${finding.code} ${finding.detail}`).join("; ")).join(" / ");
    lines.push("| " + [isFirst ? batch.frames : "", batch.step, formatDuration(batch.duration_s),
      `${batch.prompt_tokens.toLocaleString("en")} / ${batch.output_tokens.toLocaleString("en")}`,
      batch.status + (batch.attempts > 1 ? ` (${batch.attempts} attempts)` : ""),
      batch.rejections.length ? rejections : "0"].map(formatMarkdownCell).join(" | ") + " |");
  }
  return lines.join("\n");
}

// A rejection's findings as their codes, each once with how often it came ("MODEL_CALL_FAILED ×5"): one frame's failure
// repeated for each frame of its batch filled a phone's screen (the owner: "too big and not needed"); Copy keeps them whole
function countFindingCodes(findings: { code: string }[]) {
  const counts = new Map<string, number>();
  for (const finding of findings) counts.set(finding.code, (counts.get(finding.code) ?? 0) + 1);
  return [...counts].map(([code, count]) => ({ code, count }));
}

// "2 of 3 attempts": a step stopped by a failed call records its rejections with no attempt counted, which read "2 of 1"
function formatRejectedAttempts(batch: BatchHistoryEntry) {
  return `${batch.rejections.length} of ${Math.max(batch.attempts, batch.rejections.length)} attempts`;
}

// A fast-processing run's session (`fast-notes-000-004`): its frames described with no check, so no rejection
function isFastSession(batch: BatchHistoryEntry) {
  return batch.slug.startsWith("fast-notes-");
}

// `unitTitle` names what a batch holds: frames, or a PDF's pages or figures, as the card above the table does
// `onOpenStep`: a rejected row opens What the worker saw on its step; `onOpenSession`: a fast session's step opens its
// worker's turns, thinking and chat, as a phase 7 part's status does (the owner)
export default function BatchHistory({ runBase, refreshKey, unitTitle = "Frames", onOpenStep, onOpenSession }: { runBase: string;
  refreshKey: string; unitTitle?: string; onOpenStep?: (slug: string) => void; onOpenSession?: (slug: string) => void }) {
  const [batches, setBatches] = useState<BatchHistoryEntry[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let isCurrent = true;
    fetchJson<BatchHistoryEntry[]>(`${runBase}/batches`)
      .then((answer) => { if (isCurrent) { setBatches(answer); setError(""); } })
      .catch((problem) => { if (isCurrent) setError(describeError(problem)); });
    return () => { isCurrent = false; };
  }, [runBase, refreshKey]);

  if (error) return <p className="error">{error}</p>;
  if (!batches) return <Spinner />;
  if (!batches.length) return <p className="muted">No batch has run yet.</p>;
  const refused = batches.filter((batch) => batch.rejections.length > 0).length;
  const rejectionCount = batches.reduce((sum, batch) => sum + batch.rejections.length, 0);
  // nothing is checked in a fast run, so a column of zeros says nothing
  const hasRejectionColumn = !batches.every(isFastSession);

  return (
    <div className="batch-history">
      <div className="batch-history-bar">
        <p className="muted">
          {batches.length} step{batches.length === 1 ? "" : "s"} · {formatDuration(batches.reduce((sum, batch) => sum + batch.duration_s, 0))} in all
          {refused > 0 && ` · ${refused} rejected at least once`}
        </p>
        <CopyButton title="Copy the table as markdown" getText={() => formatTableAsMarkdown(batches, rejectionCount, unitTitle)} />
      </div>
      <div className="table-scroll">
        <table className={hasRejectionColumn ? "" : "no-rejections"}>
          <thead>
            <tr><th>{unitTitle}</th><th>Step</th><th>Took</th><th>Tokens in / out</th><th>Result</th>
              {hasRejectionColumn && <th>Rejections ({rejectionCount})</th>}</tr>
          </thead>
          <tbody>
            {groupRows(orderNewestBatchFirst(batches)).map(({ batch, group, isFirst }) => (
              <tr key={`${batch.slug}-${batch.started_at}`}
                className={[batch.rejections.length ? "refused" : "", group % 2 ? "alt" : "", isFirst ? "batch-start" : ""].join(" ").trim()}>
                <td data-label={unitTitle} className={isFirst ? "" : "empty-cell"}>{isFirst ? batch.frames : ""}</td>
                <td data-label="Step">{onOpenSession && isFastSession(batch) ? (
                  <button type="button" className="link-button session-link" title={`what the worker thought and wrote for frames ${batch.frames}`}
                    onClick={() => onOpenSession(batch.slug)}>
                    <Icon name="eye" size={13} /> {batch.step}
                  </button>
                ) : batch.step}
                  {onOpenStep && batch.rejections.length > 0 && (
                    <button type="button" className="btn ghost icon-only tiny" aria-label={`see what the worker saw for ${batch.step} of ${batch.frames}`}
                      title="what the worker was sent for this step, and why it was rejected" onClick={() => onOpenStep(batch.slug)}>
                      <Icon name="eye" size={14} />
                    </button>
                  )}
                </td>
                <td data-label="Took" title={formatDateTime(batch.started_at, { withYear: true, withSeconds: true })}>{formatDuration(batch.duration_s)}</td>
                <td data-label="Tokens in / out">{batch.prompt_tokens.toLocaleString("en")} / {batch.output_tokens.toLocaleString("en")}</td>
                <td data-label="Result">{batch.status}{batch.attempts > 1 ? ` (${batch.attempts} attempts)` : ""}</td>
                {hasRejectionColumn && <td data-label={`Rejections (${rejectionCount})`}>
                  <span className={batch.rejections.length ? "refused-count" : "muted"}>
                    {batch.rejections.length ? formatRejectedAttempts(batch) : "0"}
                  </span>
                  {batch.rejections.map((rejection, index) => (
                    <div key={index} className="rejection"
                      title={rejection.findings.map((finding) => `${finding.code} ${finding.detail}`).join("\n")}>
                      attempt {rejection.attempt}: {countFindingCodes(rejection.findings).map(({ code, count }) => (
                        <span key={code}><code>{code}</code>{count > 1 ? ` ×${count}` : ""} </span>
                      ))}
                    </div>
                  ))}
                </td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
