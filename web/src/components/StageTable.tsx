import { useState } from "react";
import { buildFileUrl, formatDuration } from "../api";
import type { StageProgress } from "../api";
import { STAGE_TITLES, countStageFrames, formatPace } from "./runProgress";

// Phase 1's or 2's stages in a table under its bar: each stage's status, frames in, removed and left, its time and its
// time per frame; a filter's removed frames open under its row, each with what holds it and why. Phase 3's twin is
// BatchHistory, phase 4's and 5's DocumentSteps.
// A PDF's phase 1 counts pages, step by step: each step's pages read, its time, its time per page and the time left at
// that pace (the owner: a 6,761-page PDF stood at 6,761 of 6,761 for hours with no sign of the step it was in)
function PageStageTable({ stages }: { stages: StageProgress[] }) {
  return (
    <div className="batch-history"><div className="table-scroll">
      <table className="stage-table" aria-label="stages of reading the PDF">
        <thead><tr><th>Stage</th><th>Status</th><th>Pages</th><th>Time</th><th>Time per page</th><th>Time left</th></tr></thead>
        <tbody>
          {stages.map((stageRow, index) => {
            const total = stageRow.total ?? 0;
            const done = Math.round(stageRow.done);
            return (
              <tr key={stageRow.name}>
                <td>{index + 1}. {STAGE_TITLES[stageRow.name] ?? stageRow.name}</td>
                <td>{stageRow.status}</td>
                <td className="nowrap">{total ? `${done.toLocaleString("en")} of ${total.toLocaleString("en")}` : "-"}</td>
                <td className="nowrap">{stageRow.seconds > 0 ? formatDuration(stageRow.seconds) : "-"}</td>
                <td className="nowrap">{done > 0 && stageRow.seconds > 0 ? formatPace(stageRow.seconds / done) : "-"}</td>
                <td className="nowrap">{stageRow.seconds_left != null ? formatDuration(stageRow.seconds_left) : "-"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div></div>
  );
}

export default function StageTable({ runBase, stages, phaseNumber }: { runBase: string; stages: StageProgress[]; phaseNumber: number }) {
  // the stage whose removed frames are open under its row
  const [removedOpen, setRemovedOpen] = useState("");
  if (phaseNumber === 1 && stages.some((stageRow) => stageRow.unit === "pages")) return <PageStageTable stages={stages} />;
  return (
    <div className="batch-history"><div className="table-scroll">
      <table className="stage-table" aria-label={`stages of ${phaseNumber === 1 ? "transcribing" : "preparing the frames"}`}>
        <thead><tr><th>Stage</th><th>Status</th><th>Frames in</th><th>Removed</th><th>Left</th><th>Time</th>
          <th>Time per frame</th></tr></thead>
        <tbody>
          {stages.map((stageRow, index) => {
            const title = STAGE_TITLES[stageRow.name] ?? stageRow.name;
            const isFilter = stageRow.unit === "frames";
            const frames = stageRow.frames ?? (isFilter ? stageRow.done : null);
            const { removedCount, left } = countStageFrames(stageRow);
            const removed = stageRow.removed;
            const isRemovedOpen = removedOpen === stageRow.name;
            return [
              <tr key={stageRow.name}>
                <td>{index + 1}. {title}</td>
                <td>{stageRow.status === "running" && isFilter
                  ? `running · ${Math.round(stageRow.done)} of ${stageRow.total ?? "?"}` : stageRow.status}</td>
                <td>{isFilter ? stageRow.total ?? "-" : "-"}</td>
                <td>{removed && removed.length > 0 ? (
                  <button type="button" className="link-button" aria-expanded={isRemovedOpen}
                          title={`${isRemovedOpen ? "hide" : "show"} the frames ${title} removed`}
                          onClick={() => setRemovedOpen(isRemovedOpen ? "" : stageRow.name)}>
                    {removed.length}
                  </button>
                ) : removedCount ?? "-"}</td>
                <td>{left ?? "-"}</td>
                <td className="nowrap">{stageRow.seconds > 0 ? formatDuration(stageRow.seconds) : "-"}</td>
                <td className="nowrap">{frames ? formatPace(stageRow.seconds / frames) : "-"}</td>
              </tr>,
              removed && isRemovedOpen && (
                <tr key={`${stageRow.name}-removed`} className="removed-frames">
                  <td colSpan={7}>
                    <ul aria-label={`frames ${title} removed`}>
                      {removed.map((frame) => (
                        <li key={frame.path}>
                          <img src={buildFileUrl(runBase, frame.path)} alt={frame.slug} loading="lazy" />
                          <strong>{frame.slug}</strong>
                          {frame.covered_by.length > 0 && <small>held by {frame.covered_by.join(", ")}</small>}
                          {frame.reason && <small>{frame.reason}</small>}
                        </li>
                      ))}
                    </ul>
                  </td>
                </tr>
              ),
            ];
          })}
        </tbody>
      </table>
    </div></div>
  );
}
