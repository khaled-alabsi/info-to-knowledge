import { useEffect, useState } from "react";
import { describeError, fetchJson, formatSize, sendJson } from "../api";
import type { TrashEntry } from "../api";
import { moveBack } from "../inboxMoves";
import { toggleInSet } from "../sets";
import Dialog from "./Dialog";
import Icon from "./Icon";

// The inbox's trash: what was moved to inbox/.trash, each entry restored where it came from, or ticked and deleted for
// good after one confirmation that names each. It reads the trash again whenever `refreshKey` changes, which the
// inbox bumps after moving rows into it.
export default function InboxTrash({ refreshKey, onChanged, onMessage }: {
  refreshKey: number;
  onChanged: () => void;
  onMessage: (text: string, isError?: boolean) => void;
}) {
  const [trash, setTrash] = useState<TrashEntry[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  // the entries ticked for Delete permanently, and whether its confirmation is open
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [isDeleteAsked, setIsDeleteAsked] = useState(false);
  const tickedTrash = trash.filter((entry) => ticked.has(entry.name));

  // A trash that cannot be read keeps what it showed and says why, never an empty list that reads as "nothing there"
  const reload = () => fetchJson<TrashEntry[]>("/inbox/trash").then(setTrash)
    .catch((error) => onMessage(`The trash could not be read: ${describeError(error)}`, true));
  useEffect(() => {
    void reload();
  }, [refreshKey]);

  const restore = async (name: string) => {
    try {
      const outcome = await moveBack("trash", name);
      const isRunFolder = Boolean(trash.find((entry) => entry.name === name)?.run_folder);
      onMessage(outcome.error || `Restored ${outcome.restored_as} ${isRunFolder ? "to its run folders" : "to the inbox"}`,
        Boolean(outcome.error));
      // a tick belongs to the entry it was made on: one trashed again under the same name starts unticked
      if (!outcome.error) setTicked((previous) => {
        const next = new Set(previous);
        next.delete(name);
        return next;
      });
      onChanged();
      void reload();
    } catch (error) {
      onMessage(describeError(error), true);
    }
  };

  const deleteForGood = async () => {
    setIsDeleteAsked(false);
    try {
      const outcomes = await sendJson<{ name: string; error: string }[]>("/inbox/trash/delete",
        { names: tickedTrash.map((entry) => entry.name) });
      const failed = outcomes.filter((outcome) => outcome.error);
      onMessage(failed.length ? failed.map((outcome) => outcome.error).join("; ")
        : `Deleted ${outcomes.length} for good from inbox/.trash`, failed.length > 0);
      setTicked(new Set());
      void reload();
      onChanged();
    } catch (error) {
      onMessage(describeError(error), true);
    }
  };

  if (!trash.length) return null;

  return (
    <div className="trash">
      <button className="trash-head" onClick={() => setIsOpen(!isOpen)} aria-expanded={isOpen}>
        <Icon name="trash" size={14} /> Trash <span className="count">{trash.length}</span>
        <span className={`caret${isOpen ? " open" : ""}`}><Icon name="chevron" size={14} /></span>
      </button>
      {isOpen && (
        <div className="trash-actions">
          <label className="check">
            <input type="checkbox" aria-label="select everything in the trash"
              checked={tickedTrash.length === trash.length}
              onChange={(event) => setTicked(event.target.checked ? new Set(trash.map((entry) => entry.name)) : new Set())} />
            <span>Select all</span>
          </label>
          <button className="btn danger small" disabled={!tickedTrash.length} onClick={() => setIsDeleteAsked(true)}>
            <Icon name="trash" size={14} /> Delete permanently
          </button>
        </div>
      )}
      {isOpen && (
        <ul className="rows">
          {trash.map((entry) => (
            <li key={entry.name} className="row trashed">
              <input type="checkbox" aria-label={`select ${entry.name}`} checked={ticked.has(entry.name)}
                onChange={() => setTicked((previous) => toggleInSet(previous, entry.name))} />
              <span className="row-main">
                <span className="truncate" title={entry.name}>{entry.name}</span>
                <small className="muted">{entry.run_folder ? "run folder · " : entry.is_folder ? "meeting folder · " : ""}{formatSize(entry.size_bytes)}</small>
              </span>
              <button className="btn ghost small" onClick={() => void restore(entry.name)}>
                <Icon name="restore" size={14} /> Restore
              </button>
            </li>
          ))}
        </ul>
      )}

      {isDeleteAsked && (
        <Dialog title="Delete permanently?" onClose={() => setIsDeleteAsked(false)}>
          <p>These {tickedTrash.length} item(s) are deleted for good. Restore can no longer bring them back.</p>
          <ul className="listing-names">
            {tickedTrash.map((entry) => (
              <li key={entry.name}>
                {entry.name}
                {entry.run_folder && <strong> · a whole run folder: its document and every phase file</strong>}
              </li>
            ))}
          </ul>
          <div className="dialog-actions">
            <button className="btn ghost" onClick={() => setIsDeleteAsked(false)}>Keep them</button>
            <button className="btn danger-solid" onClick={() => void deleteForGood()}>
              <Icon name="trash" /> Delete permanently
            </button>
          </div>
        </Dialog>
      )}
    </div>
  );
}
