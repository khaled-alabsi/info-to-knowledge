import { useEffect, useRef, useState } from "react";
import { describeError, fetchJson, formatDuration } from "../api";
import type { DocumentStep } from "../api";
import Icon from "./Icon";
import { formatMarkdownCell } from "../markdownTable";
import { countFindingCodes } from "../findingCounts";
import CopyButton from "./CopyButton";
import Spinner from "./Spinner";

// The table under the document writing card: every phase 4 step in the order it is asked, grouped by stage, with its
// status, model calls, time and tokens, and each refused answer with why. Read from the run's logs, so a server restart
// or a resume loses none of it; the phase 3 twin is BatchHistory.
// `isLive`: while the run works, the first step not accepted is the one being worked on, as the bar above says;
// `source`: the route of the phase's steps, phase 4's `document-steps` or phase 5's `review-steps`
// The table as markdown, one row per step, its rejections in the last cell, so it pastes whole into a chat or a note
function formatStepsAsMarkdown(steps: DocumentStep[], rejectionCount: number): string {
  const lines = [`| Stage | Step | Status | Calls | Took | Tokens in / out | Rejections (${rejectionCount}) |`,
    "| --- | --- | --- | --- | --- | --- | --- |"];
  for (const step of steps) {
    const rejections = step.rejections.map((rejection, number) => `${number + 1}: `
      + rejection.findings.map((finding) => `${finding.code} ${finding.text}`).join("; ")).join(" / ");
    lines.push("| " + [step.stage, step.step, step.status, String(step.calls), step.calls ? formatDuration(step.seconds) : "",
      step.calls ? `${step.prompt_tokens.toLocaleString("en")} / ${step.output_tokens.toLocaleString("en")}` : "", rejections]
      .map(formatMarkdownCell).join(" | ") + " |");
  }
  return lines.join("\n");
}

// `onOpenStep`: a rejected row opens What the worker saw on its step
export default function DocumentSteps({ runBase, refreshKey, isLive = false, source = "document-steps", onOpenStep }: { runBase: string;
  refreshKey: string; isLive?: boolean; source?: "document-steps" | "review-steps"; onOpenStep?: (slug: string) => void }) {
  const [steps, setSteps] = useState<DocumentStep[] | null>(null);
  const [error, setError] = useState("");
  // the number of the newest request whose answer is shown: a live run refreshes on every update, so an answer
  // still on its way when the next refresh starts is kept, and dropped only when a newer one is already shown, the
  // run or the table changed, or the table left the page
  const asked = useRef(0);
  const shown = useRef(0);
  const route = `${runBase}/${source}`;
  const shownRoute = useRef(route);
  const isOnPage = useRef(true);

  useEffect(() => {
    isOnPage.current = true;
    return () => { isOnPage.current = false; };
  }, []);

  useEffect(() => {
    if (shownRoute.current !== route) {
      shownRoute.current = route;
      shown.current = asked.current;
      setSteps(null);
    }
    const number = ++asked.current;
    const showIfNewest = (show: () => void) => {
      if (!isOnPage.current || shownRoute.current !== route || number < shown.current) return;
      shown.current = number;
      show();
    };
    fetchJson<DocumentStep[]>(route)
      .then((answer) => showIfNewest(() => { setSteps(answer); setError(""); }))
      .catch((problem) => showIfNewest(() => setError(describeError(problem))));
  }, [route, refreshKey]);

  if (error) return <p className="error">{error}</p>;
  if (!steps) return <Spinner />;
  if (!steps.length) return <p className="muted">No step has been asked yet.</p>;
  const rejectionCount = steps.reduce((sum, step) => sum + step.rejections.length, 0);
  // the step being worked on while the run works: the first not accepted
  const workedAt = steps.findIndex((step) => step.status !== "accepted");
  const seconds = steps.reduce((sum, step) => sum + step.seconds, 0);

  return (
    <div className="batch-history">
      <div className="batch-history-bar">
        <p className="muted">
          {steps.filter((step) => step.status === "accepted").length} of {steps.length} steps accepted · {formatDuration(seconds)} of
          step time, counted per step · {rejectionCount} rejection{rejectionCount === 1 ? "" : "s"}
        </p>
        <CopyButton title="Copy the table as markdown" getText={() => formatStepsAsMarkdown(steps, rejectionCount)} />
      </div>
      <div className="table-scroll">
        <table className="document-steps">
          <thead>
            <tr><th>Stage</th><th>Step</th><th>Status</th><th>Calls</th><th>Took</th><th>Tokens in / out</th><th>Rejections ({rejectionCount})</th></tr>
          </thead>
          <tbody>
            {steps.map((step, index) => {
              const isFirst = index === 0 || steps[index - 1].stage !== step.stage;
              return (
                <tr key={step.slug} className={[step.rejections.length ? "refused" : "", isFirst ? "batch-start" : ""].join(" ").trim()}>
                  <td data-label="Stage" className={isFirst ? "" : "empty-cell"}>{isFirst ? step.stage : ""}</td>
                  <td data-label="Step">{step.step}
                    {onOpenStep && step.rejections.length > 0 && (
                      <button type="button" className="btn ghost icon-only tiny" aria-label={`see what the worker saw for ${step.step}`}
                        title="what the worker was sent for this step, and why it was rejected" onClick={() => onOpenStep(step.slug)}>
                        <Icon name="eye" size={14} />
                      </button>
                    )}
                  </td>
                  <td data-label="Status">{isLive && index === workedAt && step.status === "waiting" ? "running" : step.status}</td>
                  <td data-label="Calls">{step.calls}</td>
                  <td data-label="Took" className="nowrap">{step.calls ? formatDuration(step.seconds) : ""}</td>
                  <td data-label="Tokens in / out" className="nowrap">{step.calls ? `${step.prompt_tokens.toLocaleString("en")} / ${step.output_tokens.toLocaleString("en")}` : ""}</td>
                  <td data-label={`Rejections (${rejectionCount})`} className="wide-cell">
                    <span className={step.rejections.length ? "refused-count" : "muted"}>{step.rejections.length}</span>
                    {step.rejections.map((rejection, number) => (
                      <div key={number} className="rejection"
                        title={rejection.findings.map((finding) => `${finding.code} ${finding.text}`).join("\n")}>
                        {number + 1}: {countFindingCodes(rejection.findings).map(({ code, count }) => (
                          <span key={code}><code>{code}</code>{count > 1 ? ` ×${count}` : ""} </span>
                        ))}
                      </div>
                    ))}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
