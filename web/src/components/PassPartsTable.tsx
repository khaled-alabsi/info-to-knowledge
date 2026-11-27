import { useState } from "react";
import { formatDuration } from "../api";
import type { PassPart } from "../api";
import PassPartLive from "./PassPartLive";

// "1 figure", "3 figures"
function countOf(count: number, noun: string): string {
  return `${count.toLocaleString("en")} ${noun}${count === 1 ? "" : "s"}`;
}

// What a part's conversation took to stay within its size limit: its compactions and its worker's prunes, said only
// when there were any
function describeUpkeep(part: PassPart): string {
  return [part.compactions ? countOf(part.compactions, "compaction") : "", part.prunes ? countOf(part.prunes, "prune") : ""]
    .filter(Boolean).join(" · ");
}

// Phase 7's parts, one row each: its status, size (tokens, sections, figures), model calls (compactions and prunes) and time. A part's name opens the document
// as that part left it, or as it stands while it runs (the owner: "clicking on the part should open the part
// document"); its status opens the part's live panel under the table, its steps and its thinking as the model writes
// it (the owner: "when i click, its open details of thinking, doing, live what the agent is doing or thinking"); a
// waiting part has done nothing yet, so its status opens nothing
export default function PassPartsTable({ runBase, parts, onOpenPart, linkFolder }: { runBase: string; parts: PassPart[];
  onOpenPart: (path: string) => void; linkFolder?: string }) {
  const [openNumber, setOpenNumber] = useState<number | null>(null);
  const openPart = parts.find((part) => part.number === openNumber);
  return (
    <div className="batch-history">
      <div className="table-scroll">
        <table className="stage-table" aria-label="parts of the document pass">
          {/* five columns: a part's size holds its sections and figures on a small second line, its calls the
              compactions and prunes when it had any (the owner: figures and prunes shown, "not make the table much
              bigger with more fields") */}
          <thead><tr><th>Part</th><th>Status</th><th>Size</th><th>Calls</th><th>Time</th></tr></thead>
          <tbody>
            {parts.map((part) => (
              <tr key={part.number}>
                <td className="part-name">{part.document ? (
                  <button type="button" className="link-button"
                    title={part.status === "done" ? `the document after part ${part.number}` : `the document so far, in part ${part.number}`}
                    onClick={() => onOpenPart(part.document!)}>part {part.number}</button>
                ) : <span>part {part.number}</span>}</td>
                <td>{part.status === "waiting" ? part.status : (
                  <button type="button" className="link-button" aria-expanded={openNumber === part.number}
                    title={`what part ${part.number}'s worker did and is thinking`}
                    onClick={() => setOpenNumber(openNumber === part.number ? null : part.number)}>
                    {part.status}{part.host_wait
                      ? ` · waiting for the model host for ${formatDuration(part.host_wait_seconds ?? 0)}: ${part.host_wait}`
                      : part.thinking
                        ? ` · writing for ${formatDuration(part.thinking_seconds ?? 0)}, ${(part.thinking_words ?? 0).toLocaleString("en")} words of thinking`
                        : ""}
                    {part.status === "running" && <span className="live-mark">live</span>}
                  </button>
                )}</td>
                <td>{(part.tokens ?? 0).toLocaleString("en")} tokens
                  <small className="cell-detail">{countOf(part.sections ?? 0, "section")} · {countOf(part.figures ?? 0, "figure")}</small></td>
                <td>{part.status === "waiting" ? "-" : <>{part.calls}
                  {describeUpkeep(part) && <small className="cell-detail">{describeUpkeep(part)}</small>}</>}</td>
                <td className="nowrap">{part.seconds > 0 ? formatDuration(part.seconds) : "-"}
                  {part.tokens_per_second ? <small className="cell-detail"
                    title="its calls' average writing speed, output tokens a second">{part.tokens_per_second} tok/s</small> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {openPart && <PassPartLive runBase={runBase} number={openPart.number} isRunning={openPart.status === "running"}
        onClose={() => setOpenNumber(null)} document={openPart.document} linkFolder={linkFolder} />}
    </div>
  );
}
