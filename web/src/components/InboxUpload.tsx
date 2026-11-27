import { useEffect, useRef, useState } from "react";
import type { MutableRefObject, ReactNode, RefObject } from "react";
import { createPortal } from "react-dom";
import { collectDroppedFiles, describeError, uploadBundle, uploadFiles } from "../api";
import type { UploadEntry, UploadOutcome } from "../api";
import Icon from "./Icon";

interface Upload {
  id: number;
  names: string;
  fraction: number;
  outcome: string;
  isError: boolean;
  // the files not taken, shown grouped by why
  refused?: UploadOutcome[];
  // set when its time is up: the row fades out over FADE_MS and is then taken off the list
  isFading?: boolean;
}

// How long a finished upload stays before it fades, and how long the fade takes (the owner: 10 seconds, "fade out
// nicely and slowly"). A row holding an error or a refused file stays until it is dismissed: it says what was not
// taken, and a reader who looked away would never learn it.
const UPLOAD_SHOWN_MS = 10000;
const FADE_MS = 1500;

// The groups a refused file falls into, in the order they are shown.
const REFUSAL_GROUPS: { kind: UploadOutcome["kind"]; title: string }[] = [
  { kind: "in_inbox", title: "Skipped: already in the inbox" },
  { kind: "in_upload", title: "Skipped: twice in this upload" },
  { kind: "processed", title: "Skipped: processed already" },
  { kind: "refused", title: "Not taken" },
];

// What another part of the panel asks of the upload: open its file picker for a person's own list, whose id comes
// back with the names saved
export interface UploadHandle {
  pick: (listId: string) => void;
}

// The file or folder name an upload saved, from the server's path or name
const toSavedName = (saved: string) => saved.split("/").filter(Boolean).pop() ?? saved;

function summariseUpload(saved: string[], refused: UploadOutcome[]): string {
  return [saved.length ? `saved ${saved.join(", ")}` : "nothing saved", refused.length ? `skipped ${refused.length}` : ""]
    .filter(Boolean).join(" · ");
}

// The inbox's upload: the drop zone and its file picker, and a row per upload with its progress and what was refused.
export default function InboxUpload({ onChanged, onMessage, onOpenRun, panel, isZoneShown = true, beside, handle,
  onSaved }: {
  onChanged: () => void;
  // set to what opens the picker for a list; the names saved by an upload started there come back with that list's id
  handle?: MutableRefObject<UploadHandle | null>;
  onSaved?: (names: string[], listId: string) => void;
  onMessage: (text: string, isError?: boolean) => void;
  onOpenRun: (root: string, name: string) => void;
  // the panel a file can be dropped anywhere on: while one is dragged over it, an upload zone covers it whole
  panel?: RefObject<HTMLElement | null>;
  // false while the search takes the zone's place; the uploads' rows and the panel's drop stay
  isZoneShown?: boolean;
  // what stands beside the zone, on its row (the search button)
  beside?: ReactNode;
}) {
  const [isDragging, setIsDragging] = useState(false);
  // the panel's place on screen while a file is dragged over it, for the zone drawn over it; null otherwise
  const [panelBox, setPanelBox] = useState<DOMRect | null>(null);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const nextUploadId = useRef(0);
  // The ids whose timers are already running, so a re-render never arms a second pair for one row.
  const fading = useRef<Map<number, number[]>>(new Map());
  const picker = useRef<HTMLInputElement>(null);
  // the list the open picker uploads into (null: the inbox alone)
  const pickTarget = useRef<string | null>(null);

  useEffect(() => {
    if (!handle) return;
    handle.current = { pick: (listId) => {
      pickTarget.current = listId;
      picker.current?.click();
    } };
  }, [handle]);

  // A finished upload leaves by itself: it fades after UPLOAD_SHOWN_MS and is off the list FADE_MS later. The timers
  // of a row already counting down are NOT cleared when the list changes - another upload starting would otherwise
  // leave the first row faded and never removed - so they are kept per row and cleared only when the panel goes.
  useEffect(() => {
    uploads
      .filter((upload) => upload.outcome && !upload.isError && !upload.refused?.length && !fading.current.has(upload.id))
      .forEach((upload) => fading.current.set(upload.id, [
        window.setTimeout(() => setUploads((previous) =>
          previous.map((other) => (other.id === upload.id ? { ...other, isFading: true } : other))), UPLOAD_SHOWN_MS),
        window.setTimeout(() => {
          fading.current.delete(upload.id);
          setUploads((previous) => previous.filter((other) => other.id !== upload.id));
        }, UPLOAD_SHOWN_MS + FADE_MS),
      ]));
  }, [uploads]);

  useEffect(() => () => fading.current.forEach((timers) => timers.forEach(window.clearTimeout)), []);

  // A new row at 0%, and the function that changes it as the upload goes
  const addUploadRow = (names: string) => {
    const id = nextUploadId.current++;
    setUploads((previous) => [...previous, { id, names, fraction: 0, outcome: "", isError: false }]);
    return (change: Partial<Upload>) =>
      setUploads((previous) => previous.map((upload) => (upload.id === id ? { ...upload, ...change } : upload)));
  };

  // One video dropped alone goes straight into the inbox. Several files, a folder or a zip go in too, each recording as
  // its own, with no question (the owner: asking which are one meeting at every upload was "annoying"); recordings that
  // are one meeting are merged afterwards with the inbox's merge button.
  const startUpload = async (entries: UploadEntry[], listId: string | null = null) => {
    if (!entries.length) return;
    const isOneVideo = entries.length === 1 && !entries[0].path.includes("/") && !/\.zip$/i.test(entries[0].file.name);
    if (!isOneVideo) {
      await startBundle(entries, listId);
      return;
    }
    const update = addUploadRow(entries[0].file.name);
    try {
      const outcomes = await uploadFiles(entries, (fraction) => update({ fraction }));
      const refused = outcomes.filter((outcome) => outcome.error);
      update({
        fraction: 1,
        outcome: refused.length ? summariseUpload(outcomes.filter((outcome) => !outcome.error).map((outcome) => outcome.saved_as), refused)
          : `saved as ${outcomes.map((outcome) => outcome.saved_as).join(", ")}`,
        isError: refused.length > 0,
        refused,
      });
      const saved = outcomes.filter((outcome) => !outcome.error).map((outcome) => toSavedName(outcome.saved_as));
      if (listId && saved.length) onSaved?.(saved, listId);
      onChanged();
    } catch (error) {
      update({ outcome: describeError(error), isError: true });
    }
  };

  const startBundle = async (entries: UploadEntry[], listId: string | null) => {
    const update = addUploadRow(entries.length === 1 ? entries[0].path : `${entries.length} files`);
    try {
      const outcome = await uploadBundle(entries, (fraction) => update({ fraction }));
      update({ fraction: 1, isError: outcome.refused.length > 0, refused: outcome.refused,
        outcome: summariseUpload(outcome.added, outcome.refused) });
      const saved = outcome.added.map(toSavedName);
      if (listId && saved.length) onSaved?.(saved, listId);
      onChanged();
    } catch (error) {
      update({ outcome: describeError(error), isError: true });
    }
  };

  // A file dragged anywhere over the panel: the whole panel becomes the drop zone, and a drop there uploads it
  const startUploadRef = useRef(startUpload);
  startUploadRef.current = startUpload;
  useEffect(() => {
    const element = panel?.current;
    if (!element) return;
    const isFileDrag = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes("Files");
    const onOver = (event: DragEvent) => {
      if (!isFileDrag(event)) return;
      event.preventDefault();
      setPanelBox(element.getBoundingClientRect());
    };
    const onLeave = (event: DragEvent) => {
      if (!element.contains(event.relatedTarget as Node | null)) setPanelBox(null);
    };
    // a drop is taken whatever its types say: a browser lists none for some drops, and the drop must not open the file
    const onDrop = (event: DragEvent) => {
      if (!event.dataTransfer) return;
      event.preventDefault();
      setPanelBox(null);
      collectDroppedFiles(event.dataTransfer).then((entries) => startUploadRef.current(entries))
        .catch((error) => onMessage(`The drop could not be read: ${describeError(error)}`, true));
    };
    element.addEventListener("dragover", onOver);
    element.addEventListener("dragleave", onLeave);
    element.addEventListener("drop", onDrop);
    return () => {
      element.removeEventListener("dragover", onOver);
      element.removeEventListener("dragleave", onLeave);
      element.removeEventListener("drop", onDrop);
    };
  }, [panel, onMessage]);

  return (
    <>
      {panelBox && createPortal(
        <div className="panel-dropzone" style={{ top: panelBox.top, left: panelBox.left, width: panelBox.width, height: panelBox.height }}>
          <span className="drop-icon"><Icon name="upload" size={28} /></span>
          <strong>Drop to upload into the inbox</strong>
        </div>,
        document.body,
      )}
      {isZoneShown && <div className="inbox-top">
      <div
        className={`dropzone${isDragging ? " dragging" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setIsDragging(false);
          if (panel) return;
          collectDroppedFiles(event.dataTransfer).then(startUpload)
            .catch((error) => onMessage(`The drop could not be read: ${describeError(error)}`, true));
        }}
        onClick={() => {
          pickTarget.current = null;
          picker.current?.click();
        }}
        role="button"
        tabIndex={0}
        onKeyDown={(event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          pickTarget.current = null;
          picker.current?.click();
        }}
      >
        <span className="drop-icon"><Icon name="upload" size={20} /></span>
        <span>
          <strong>Drop recordings</strong> or <u>browse</u>
          <small>They are uploaded into the inbox.</small>
        </span>
      </div>
      {beside}
      </div>}
      {/* outside the zone, so a list's upload button opens it while the search takes the zone's place */}
      <input ref={picker} type="file" accept="video/*,audio/*,.pdf,.docx,.odt,.pptx,.odp,.mkv,.ts,.m4a,.opus,.zip,.vtt,.srt" multiple hidden
        data-testid="upload-picker"
        onChange={(event) => {
          const listId = pickTarget.current;
          pickTarget.current = null;
          void startUpload(Array.from(event.target.files ?? []).map((file) => ({ file, path: file.name })), listId);
          event.target.value = "";
        }} />

      {uploads.length > 0 && (
        <ul className="uploads">
          {uploads.map((upload) => (
            <li key={upload.id} className={upload.isFading ? "fading" : undefined}>
              <div className="upload-head">
                <span className="truncate" title={upload.names}>{upload.names}</span>
                {upload.fraction < 1 && !upload.outcome && <span className="muted">{Math.round(upload.fraction * 100)}%</span>}
                {upload.outcome && (
                  <button className="btn ghost icon-only" aria-label="dismiss"
                    onClick={() => setUploads((previous) => previous.filter((other) => other.id !== upload.id))}>
                    <Icon name="x" size={14} />
                  </button>
                )}
              </div>
              <div className="meter"><span style={{ width: `${upload.fraction * 100}%` }} /></div>
              {upload.outcome && <small className={upload.isError ? "error" : "muted"}>{upload.outcome}</small>}
              {REFUSAL_GROUPS.map((group) => {
                const members = (upload.refused ?? []).filter((outcome) => (outcome.kind ?? "refused") === group.kind);
                return members.length > 0 && (
                  <div key={group.kind} className="refusal-group">
                    <small className="error">{group.title} ({members.length})</small>
                    <ul>
                      {members.map((outcome) => (
                        <li key={outcome.name}>
                          <span className="truncate" title={outcome.name}>{outcome.name}</span>
                          {group.kind === "refused" ? <small className="muted">{outcome.error}</small>
                            : <small className="muted">as {outcome.same_as}</small>}
                          {outcome.duplicate_of && (
                            <button className="btn small" onClick={() => onOpenRun(outcome.duplicate_of!.root, outcome.duplicate_of!.name)}>
                              Open {outcome.duplicate_of.root}/{outcome.duplicate_of.name}
                            </button>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
