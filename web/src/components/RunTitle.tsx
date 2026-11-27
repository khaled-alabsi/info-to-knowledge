import { useRef, useState } from "react";
import Icon from "./Icon";

// A run's title with a pencil beside it: the title turns into a name box, Enter or leaving it saves the name the run is
// shown under, Escape leaves it as it was. An empty name shows the run under its folder's name again; nothing on disk
// is renamed.
export default function RunTitle({ shownName, folderName, onSave }: { shownName: string; folderName: string;
  onSave: (name: string) => Promise<void> }) {
  const [draft, setDraft] = useState<string | null>(null);
  const pencil = useRef<HTMLButtonElement>(null);

  const close = () => {
    setDraft(null);
    // the box goes; the keyboard comes back to the pencil that opened it
    window.requestAnimationFrame(() => pencil.current?.focus());
  };
  const save = async () => {
    if (draft === null) return;
    const name = draft.trim();
    if (name !== shownName) await onSave(name === folderName ? "" : name);
    close();
  };

  if (draft !== null) {
    return (
      <input type="text" className="title-input" aria-label="name shown" value={draft} autoFocus placeholder={folderName}
        onChange={(event) => setDraft(event.target.value)} onBlur={() => void save()}
        onKeyDown={(event) => {
          if (event.key === "Enter") void save();
          if (event.key === "Escape") close();
        }} />
    );
  }

  return (
    <div className="title-row">
      <h2 className="truncate" title={shownName === folderName ? folderName : `${shownName}\n${folderName}`}>{shownName}</h2>
      <button ref={pencil} className="btn ghost icon-only small" aria-label="rename"
        title="the name this run is shown under; empty shows its folder's name" onClick={() => setDraft(shownName)}>
        <Icon name="pencil" size={14} />
      </button>
    </div>
  );
}
