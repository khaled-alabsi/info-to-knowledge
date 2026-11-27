import { useEffect, useState } from "react";
import { buildRunPath, describeError, downloadDocuments, fetchJson, isRunFound, isSameName } from "../api";
import type { DownloadPart, RunSummary } from "../api";
import { toggleInSet } from "../sets";
import Dialog from "./Dialog";
import Icon from "./Icon";
import SearchBox from "./SearchBox";
import { TagInput } from "./TagInput";

// The Manager tab's Download, in the detail view: the runs with a finished document, filtered by their tags (a run shown
// has every chosen tag), ticked, and what of each to take; one zip of them all.
export default function DownloadView({ runs, onBack, onMessage }: { runs: RunSummary[]; onBack: () => void;
  onMessage: (text: string, isError?: boolean) => void }) {
  const [parts, setParts] = useState<DownloadPart[]>([]);
  const [chosenParts, setChosenParts] = useState<Set<string>>(new Set());
  const [tagFilter, setTagFilter] = useState<Set<string>>(new Set());
  // the search over the runs' names, AI names and topics
  const [search, setSearch] = useState("");
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [isBusy, setIsBusy] = useState(false);
  const [isTagListOpen, setIsTagListOpen] = useState(false);
  useEffect(() => {
    fetchJson<DownloadPart[]>("/download/parts").then((answer) => {
      setParts(answer);
      setChosenParts(new Set(answer.map((part) => part.key)));
    }).catch((error) => onMessage(`What can be downloaded could not be read: ${describeError(error)}`, true));
  }, [onMessage]);

  const withDocument = runs.filter((run) => run.has_document);
  const tags = [...new Set(withDocument.flatMap((run) => run.tags))].sort((a, b) => a.localeCompare(b));
  // A run is shown when it has every tag chosen (no tag chosen shows them all) and the search finds it
  const shown = withDocument.filter((run) => [...tagFilter].every((tag) => run.tags.includes(tag)) && isRunFound(run, search));
  const chosen = shown.filter((run) => ticked.has(buildRunPath(run)));

  const download = async () => {
    setIsBusy(true);
    try {
      const skipped = await downloadDocuments(chosen.map((run) => ({ root: run.root, name: run.name })), [...chosenParts]);
      onMessage(`Downloaded ${chosen.length - skipped.length} run${chosen.length - skipped.length === 1 ? "" : "s"}`
        + (skipped.length ? `; left out, no document: ${skipped.join(", ")}` : ""));
    } catch (problem) {
      onMessage(`Download failed: ${describeError(problem)}`, true);
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <div className="detail manager-tool">
      <header className="detail-head">
        <button className="btn ghost icon-only back" onClick={onBack} aria-label="back"><Icon name="back" /></button>
        <div className="detail-title">
          <h2>Download</h2>
          <small className="muted">Many runs' results in one zip: tick the runs{parts.length > 1 ? ", choose what of each" : ""}.</small>
        </div>
      </header>
      {/* a choice of one is no choice: the box shows once there is more than one part to choose */}
      {parts.length > 1 && <fieldset className="manager-parts">
        <legend>What to download</legend>
        {parts.map((part) => (
          <label key={part.key} className="manager-check">
            <input type="checkbox" checked={chosenParts.has(part.key)} onChange={() => setChosenParts(toggleInSet(chosenParts, part.key))} />
            {part.label}
          </label>
        ))}
      </fieldset>}
      <SearchBox label="find a run" placeholder="find a run by name or topic" value={search} onChange={setSearch} />
      {/* a box suggesting the runs' tags as they are typed, the chosen ones as chips before it: every tag as a chip
          filled the view (the owner: "there are too many tags") */}
      {tags.length > 0 && (
        <div className="manager-tags" role="group" aria-label="filter by tag">
          {[...tagFilter].map((tag) => (
            <span key={tag} className="pill tag">
              {tag}
              <button className="tag-remove" aria-label={`remove tag ${tag}`}
                onClick={() => setTagFilter(toggleInSet(tagFilter, tag))}>×</button>
            </span>
          ))}
          <TagInput className="tag-filter" label="filter by tag" given={[...tagFilter]} options={tags}
            onAdd={(added) => setTagFilter(new Set([...tagFilter, ...added]))} />
          {/* beside the box, every tag in a window of its own, for choosing by looking rather than typing (the owner) */}
          <button className="btn small" onClick={() => setIsTagListOpen(true)}>
            <Icon name="list" /> All tags ({tags.length})
          </button>
        </div>
      )}
      {isTagListOpen && (
        <Dialog title="Filter by tag" onClose={() => setIsTagListOpen(false)}>
          <p className="muted">A run is shown when it has every tag ticked.</p>
          <div className="tag-list" role="group" aria-label="every tag">
            {tags.map((tag) => (
              <button key={tag} className={`chip${tagFilter.has(tag) ? " on" : ""}`} aria-pressed={tagFilter.has(tag)}
                onClick={() => setTagFilter(toggleInSet(tagFilter, tag))}>{tag}</button>
            ))}
          </div>
          <div className="toolbar">
            <button className="btn" disabled={!tagFilter.size} onClick={() => setTagFilter(new Set())}>Clear</button>
            <button className="btn primary" onClick={() => setIsTagListOpen(false)}>Done</button>
          </div>
        </Dialog>
      )}
      {/* the action above the list, so it is in sight however many runs the list holds */}
      <div className="toolbar">
        <button className={`btn ${chosen.length && chosenParts.size ? "primary" : "muted-btn"}`}
          disabled={!chosen.length || !chosenParts.size || isBusy} onClick={() => void download()}>
          {isBusy ? <><span className="spinner" aria-hidden="true" /> Building the zip of {chosen.length} run{chosen.length === 1 ? "" : "s"}…</>
            : <><Icon name="download" /> {chosen.length ? `Download ${chosen.length}` : "Tick runs to download"}</>}
        </button>
      </div>
      <label className="manager-check all">
        <input type="checkbox" aria-label="tick every run shown" checked={shown.length > 0 && chosen.length === shown.length}
          onChange={(event) => setTicked(event.target.checked ? new Set(shown.map(buildRunPath)) : new Set())} />
        {shown.length} run{shown.length === 1 ? "" : "s"} with a document{tagFilter.size ? " and every chosen tag" : ""}
      </label>
      <ul className="manager-runs">
        {shown.map((run) => (
          <li key={buildRunPath(run)}>
            <label className={`row selectable${ticked.has(buildRunPath(run)) ? " ticked" : ""}`}>
              <input type="checkbox" checked={ticked.has(buildRunPath(run))} onChange={() => setTicked(toggleInSet(ticked, buildRunPath(run)))} />
              <span className="row-main">
                <span className="row-name" title={run.name}>{run.display_name || run.name}</span>
                {run.topic && !isSameName(run.topic, run.display_name || run.name) && (
                  <small className="topic truncate" title={run.topic}>{run.topic}</small>
                )}
                {run.tags.length > 0 && <small className="muted truncate">{run.tags.join(" · ")}</small>}
              </span>
            </label>
          </li>
        ))}
      </ul>
    </div>
  );
}

