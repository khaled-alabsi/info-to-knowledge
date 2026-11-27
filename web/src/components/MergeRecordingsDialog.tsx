import { useState } from "react";
import { describeError, formatSize, mergeRecordings } from "../api";
import type { InboxItem } from "../api";
import Dialog from "./Dialog";
import Icon from "./Icon";

// The inbox's merge: the ticked recordings that are one meeting become one recording
// in parts, played in the order shown, which is their names' natural order ("part 2" before "part 10"), the order the
// program reads the parts in. The name starts as the beginning their names share, and can be typed over.

// "Weekly part 2.mp4" without its suffix
const stemOf = (name: string) => name.replace(/\.[^.]+$/, "");

// The beginning the names share, without a trailing part word or separator; the first name's when they share none.
// The same rule as the server's `inbox_merge_service.suggest_merged_name`, which takes the name when none is sent.
export function suggestMergedName(names: string[]): string {
  const stems = names.map(stemOf);
  let prefix = stems[0] ?? "";
  for (const stem of stems) while (!stem.startsWith(prefix)) prefix = prefix.slice(0, -1);
  return prefix.replace(/(?:[\s._-]+(?:part|teil|p))?[\s._-]*$/i, "").trim() || stems[0] || "";
}

// The order the parts are read in: by their names, numbers as numbers
export function sortAsParts(items: InboxItem[]): InboxItem[] {
  return [...items].sort((one, other) => one.name.localeCompare(other.name, undefined, { numeric: true, sensitivity: "base" }));
}

export default function MergeRecordingsDialog({ items, onMerged, onClose }: {
  items: InboxItem[];
  onMerged: (name: string) => void;
  onClose: () => void;
}) {
  const parts = sortAsParts(items);
  const [name, setName] = useState(() => suggestMergedName(parts.map((item) => item.name)));
  const [error, setError] = useState("");
  const [isMerging, setIsMerging] = useState(false);
  const isNameWrong = !name.trim() || /[/\\]/.test(name) || name.trim().startsWith(".");
  const merge = async () => {
    setIsMerging(true);
    setError("");
    try {
      const answer = await mergeRecordings(parts.map((item) => item.video), name.trim());
      onMerged(answer.name);
    } catch (reason) {
      setError(describeError(reason));
      setIsMerging(false);
    }
  };
  return (
    <Dialog title={`Merge ${parts.length} recordings into one`} onClose={onClose}>
      <p className="muted">
        One meeting recorded in several files: they become one recording, its parts played one after the other in this
        order, and one run reads them as one long recording.
      </p>
      <label className="merge-name">
        <span>Name</span>
        <input type="text" value={name} aria-label="name of the merged recording" aria-invalid={isNameWrong}
          onChange={(event) => setName(event.target.value)} />
      </label>
      <ol className="merge-parts" aria-label="parts in the order they are played">
        {parts.map((item, index) => (
          <li key={item.video}>
            <span className="merge-part-number">{index + 1}</span>
            <Icon name={item.is_audio ? "wave" : "play"} size={14} />
            <span className="truncate" title={item.name}>{item.name}</span>
            <small className="muted">{formatSize(item.size_bytes)}</small>
          </li>
        ))}
      </ol>
      {isNameWrong && <p className="error">Give it a name with no slash, not starting with a dot.</p>}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="dialog-actions">
        <button className="btn" onClick={onClose}>Cancel</button>
        <button className="btn primary" disabled={isMerging || isNameWrong} onClick={() => void merge()}>
          {isMerging ? <><span className="spinner" aria-hidden="true" /> Merging…</> : <><Icon name="merge" size={14} /> Merge</>}
        </button>
      </div>
    </Dialog>
  );
}
