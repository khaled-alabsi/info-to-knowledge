import { useState } from "react";
import type { BatchMark, RecordingPart } from "../api";
import { formatSize, formatTime } from "../api";
import Icon from "./Icon";
import PartsPlayer from "./PartsPlayer";
import TranscriptView from "./TranscriptView";

// The recording the run was made from, at the same level as its progress and its artifacts; a meeting recorded in
// several files plays part after part. A frame's "Play from" hands its second of the meeting here as `seekTo`; the
// player opens the part it falls in at the right second, and `onSeeked` clears it. Beside the player, one panel as tall
// as it, with two tabs: Timestamps, phase 3's batches by title (a click plays the recording from the batch's first
// second, the batch playing is marked, and each one opens to show its summary), and Transcript, phase 1's lines (a
// line's time, or a line found by its search, plays the recording from there). Before phase 3 wrote a batch the panel
// holds the transcript alone. An audio recording's player is one bar, too short to set the panel's height, so there
// the panel stands under the player, the page's width, with a height of its own.
export default function RecordingView({ runBase, name, parts, isAudio = false, batches = [], onPlayFrom, seekTo, onSeeked }: {
  runBase: string;
  name: string;
  parts: RecordingPart[];
  isAudio?: boolean;
  batches?: BatchMark[];
  onPlayFrom?: (seconds: number) => void;
  seekTo: number | null;
  onSeeked: () => void;
}) {
  const [playingS, setPlayingS] = useState(0);
  const [openAt, setOpenAt] = useState<Set<number>>(new Set());
  const [chosenTab, setChosenTab] = useState<"timestamps" | "transcript">("timestamps");
  const tab = batches.length ? chosenTab : "transcript";
  // A PDF is read, not heard: its pages' text is no transcript (the owner)
  const isPdf = /\.pdf$/i.test(name);
  const buildSrc = (index: number) => `/api${runBase}/recording${index ? `?part=${index}` : ""}`;
  const playingAt = batches.reduce((found, batch, at) => (batch.start_s <= playingS ? at : found), -1);

  const player = (
    <PartsPlayer className="card recording" parts={parts} buildSrc={buildSrc} seekTo={seekTo} onSeeked={onSeeked}
      onTimeUpdate={setPlayingS}>
      {(index) => (
        <div className="audio-head">
          <strong className="truncate" title={parts.length > 1 ? parts[index]?.name : name}>
            {parts.length > 1 ? `${name} · ${parts[index]?.name}` : name}
          </strong>
          {/* Only a recording in parts says which part plays; one file needs no line (the owner: "not needed") */}
          {parts.length > 1 && <small className="muted">{`part ${index + 1} of ${parts.length} of the recording`}</small>}
          <a className="btn ghost small" href={buildSrc(index)} download={parts[index]?.name ?? name}
            title={`save ${parts[index]?.name ?? name}`}>
            <Icon name="download" size={14} /> Download
            {parts[index]?.size_bytes ? <span className="muted"> · {formatSize(parts[index].size_bytes)}</span> : null}
          </a>
        </div>
      )}
    </PartsPlayer>
  );
  const toggleOpen = (at: number) => setOpenAt((open) => {
    const next = new Set(open);
    if (!next.delete(at)) next.add(at);
    return next;
  });

  // A PDF is shown alone, the page's width: its text is an artifact, in the Artifacts tab (the owner: "those should not
  // be stacked, the text tab is artifact")
  if (isPdf) return player;

  // The player and the panel share one row: the panel's own height counts as none, so the player sets the row's height
  // and the panel fills it, scrolling inside. The wrapper is what the stacking rule measures: the detail view's width,
  // never the screen's (beside the lists on a laptop screen, the panel squeezed the player to a stamp)
  return (
    <div className="recording-wrap">
    <div className={`recording-layout${isAudio ? " audio" : ""}`}>
      <div className="recording-player">{player}</div>
      <section className="card recording-side">
        <div className="view-tabs side-tabs" role="tablist">
          {batches.length > 0 && (
            <button role="tab" aria-selected={tab === "timestamps"} className={tab === "timestamps" ? "on" : ""}
              onClick={() => setChosenTab("timestamps")}>Timestamps</button>
          )}
          <button role="tab" aria-selected={tab === "transcript"} className={tab === "transcript" ? "on" : ""}
            onClick={() => setChosenTab("transcript")}>{isPdf ? "Text" : "Transcript"}</button>
        </div>
        {tab === "timestamps" ? (
          <nav className="batch-list" aria-label="timestamps">
            <ol>
              {batches.map((batch, at) => (
                <li key={`${batch.start_s}-${at}`}>
                  <div className="batch-row">
                    <button className={`batch-mark${at === playingAt ? " on" : ""}`} aria-current={at === playingAt || undefined}
                      title={`play from ${formatTime(batch.start_s)}`} onClick={() => onPlayFrom?.(batch.start_s)}>
                      <span className="batch-time">{formatTime(batch.start_s)}</span>
                      <span>{batch.title || `${formatTime(batch.start_s)} to ${formatTime(batch.end_s)}`}</span>
                    </button>
                    {batch.summary && (
                      <button className={`batch-toggle caret${openAt.has(at) ? " open" : ""}`} aria-expanded={openAt.has(at)}
                        aria-label={`${openAt.has(at) ? "hide" : "show"} the summary of ${formatTime(batch.start_s)}`}
                        onClick={() => toggleOpen(at)}>
                        <Icon name="chevron" size={14} />
                      </button>
                    )}
                  </div>
                  {openAt.has(at) && <p className="batch-summary">{batch.summary}</p>}
                </li>
              ))}
            </ol>
          </nav>
        ) : (
          <TranscriptView runBase={runBase} isPdf={isPdf} playingS={playingS} onPlayFrom={(seconds) => onPlayFrom?.(seconds)} />
        )}
      </section>
    </div>
    </div>
  );
}
