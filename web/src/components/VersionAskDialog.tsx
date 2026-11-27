import type { DownloadVersion, RunDocumentVersions } from "../api";
import Dialog from "./Dialog";
import Icon from "./Icon";

// The list download's question when a ticked run holds several versions of its document:
// one card a document, its name and how many versions it has, each version a labelled tag in the order it was made, the
// first marked as the original and the last as the latest, a pass still running marked as not finished; then the three
// answers. The owner, of the comma line it replaces: "no one can read those, they should be elements with details".

const NOT_FINISHED = " (not finished)";

function VersionTag({ label, isFirst, isLast }: { label: string; isFirst: boolean; isLast: boolean }) {
  const isUnfinished = label.endsWith(NOT_FINISHED);
  const words = isUnfinished ? label.slice(0, -NOT_FINISHED.length) : label;
  const kind = isUnfinished ? " unfinished" : isLast ? " latest" : isFirst ? " original" : "";
  return (
    <li className={`version-tag${kind}`}>
      <span>{words}</span>
      {isUnfinished && <small>not finished</small>}
      {!isUnfinished && isLast && <small>latest</small>}
    </li>
  );
}

export default function VersionAskDialog({ runs, displayNames, onChoose, onClose }: {
  runs: RunDocumentVersions[];
  // the name each run is shown under in the lists, by "root/name"; a run with none is shown by its folder's name
  displayNames: Record<string, string>;
  onChoose: (version: DownloadVersion) => void;
  onClose: () => void;
}) {
  return (
    <Dialog title="Which version do you want to download?" onClose={onClose}>
      <p className="muted">
        {runs.length === 1 ? "1 document has" : `${runs.length} documents have`} several versions. A document with one
        version is downloaded as it is, whatever you choose.
      </p>
      <ul className="version-runs" aria-label="documents with several versions">
        {runs.map((run) => {
          const key = `${run.root}/${run.name}`;
          return (
            <li key={key} className="version-run">
              <div className="version-run-head">
                <Icon name="layers" size={14} />
                <strong className="truncate" title={displayNames[key] || run.name}>{displayNames[key] || run.name}</strong>
                <span className="version-count">{run.versions.length} versions</span>
              </div>
              <ol className="version-tags" aria-label={`versions of ${displayNames[key] || run.name}`}>
                {run.versions.map((version, index) => (
                  <VersionTag key={version.number} label={version.label} isFirst={index === 0}
                    isLast={index === run.versions.length - 1} />
                ))}
              </ol>
            </li>
          );
        })}
      </ul>
      <div className="dialog-actions">
        <button className="btn" onClick={onClose}>Cancel</button>
        <span className="spacer" />
        <button className="btn" onClick={() => onChoose("original")} title="each document before any change">
          The original
        </button>
        <button className="btn" onClick={() => onChoose("all")} title="every version of each document, numbered in order">
          All versions
        </button>
        <button className="btn primary" onClick={() => onChoose("latest")} title="each document as it is now">
          <Icon name="download" size={14} /> The latest
        </button>
      </div>
    </Dialog>
  );
}
