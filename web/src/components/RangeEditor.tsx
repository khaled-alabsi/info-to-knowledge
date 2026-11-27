import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent, PointerEvent } from "react";
import { fetchJson } from "../api";
import { formatClock, formatPages, parseClock, parsePages } from "../rangeTime";
import Icon from "./Icon";

// The run form's Range: the part of the input a run processes. A video shows a filmstrip of its
// frames, an audio recording its waveform, with two handles for where processing starts and ends; a PDF a strip of its
// pages, the first clicked and the last shift-clicked. Times and pages stay the input's own. An untouched range, or one
// dragged back to the whole input, sends nothing, so the run processes everything as before.

export interface RangeValue {
  range_start: string;
  range_end: string;
  range_pages: string;
}

// Where the editor's pictures come from: an inbox recording's previews (`/inbox/preview`, `path=` its file) or a run's
// (`/runs/<root>/<name>/preview`, no query), both answered by `input_preview_service`
export interface PreviewSource {
  base: string;
  query: string;
}

// A preview's address without `/api`, as `fetchJson` takes it: `/inbox/preview/frame?path=…&at=12.0`
function buildPreviewPath(source: PreviewSource, kind: "frame" | "waveform" | "page", params: string): string {
  return `${source.base}/${kind}?${source.query ? `${source.query}&` : ""}${params}`;
}

// An inbox recording's preview source
export function buildInboxPreviewSource(video: string): PreviewSource {
  return { base: "/inbox/preview", query: `path=${encodeURIComponent(video)}` };
}

const FILMSTRIP_FRAMES = 12;      // frames along a video's length: enough to recognise its parts at any width
const WAVEFORM_BARS = 160;        // bars along an audio recording's length
const MIN_SPAN_S = 1;             // the shortest range: two handles never cross
const KEY_STEP_S = 1;             // an arrow key moves a handle by a second, with Shift by ten
const KEY_BIG_STEP_S = 10;

// What the Range button says: the whole input, or the part chosen
export function describeRange(value: RangeValue, durationS: number, pageCount: number): string {
  if (pageCount) {
    const pages = parsePages(value.range_pages);
    return pages ? (pages[0] === pages[1] ? `Page ${pages[0]}` : `Pages ${pages[0]} to ${pages[1]}`) : `All ${pageCount} pages`;
  }
  if (!value.range_start && !value.range_end) return "Whole recording";
  const start = parseClock(value.range_start) ?? 0;
  const end = parseClock(value.range_end) ?? durationS;
  return `${formatClock(start)} to ${formatClock(end)}`;
}

// A typed time, committed on Enter or on leaving the box; `onCommit` answers why it is refused, "" when it is taken, and
// the refusal is shown by the caller under the row of fields
function TimeField({ label, seconds, onCommit, isInvalid }: {
  label: string;
  seconds: number;
  onCommit: (seconds: number | null) => void;
  isInvalid: boolean;
}) {
  const [draft, setDraft] = useState(formatClock(seconds));
  useEffect(() => { setDraft(formatClock(seconds)); }, [seconds]);
  const commit = () => onCommit(parseClock(draft));
  return (
    <label className={`range-field${isInvalid ? " invalid" : ""}`}>
      <span>{label}</span>
      <input type="text" inputMode="numeric" aria-label={`${label.toLowerCase()} time`} value={draft}
        aria-invalid={isInvalid}
        onChange={(event) => setDraft(event.target.value)} onBlur={commit}
        onKeyDown={(event) => { if (event.key === "Enter") commit(); }} />
    </label>
  );
}

function TimeRange({ source, durationS, isAudio, value, onChange, playerS, onSeek }: {
  source: PreviewSource;
  durationS: number;
  isAudio: boolean;
  value: RangeValue;
  onChange: (value: RangeValue) => void;
  // the player's second, null where no player stands beside the editor
  playerS: number | null;
  onSeek: (seconds: number) => void;
}) {
  const start = Math.min(parseClock(value.range_start) ?? 0, durationS);
  const end = Math.min(parseClock(value.range_end) ?? durationS, durationS);
  const track = useRef<HTMLDivElement>(null);
  const dragging = useRef<"start" | "end" | null>(null);
  const [peaks, setPeaks] = useState<number[] | null>(null);
  useEffect(() => {
    if (!isAudio) return;
    fetchJson<{ peaks: number[] }>(buildPreviewPath(source, "waveform", `bins=${WAVEFORM_BARS}`))
      .then((answer) => setPeaks(answer.peaks)).catch(() => setPeaks([]));
  }, [isAudio, source.base, source.query]);

  // A range back at the input's edges sends nothing for that edge: the whole recording is "no range"
  const save = (nextStart: number, nextEnd: number) => onChange({
    range_start: nextStart <= 0.5 ? "" : formatClock(nextStart),
    range_end: nextEnd >= durationS - 0.5 ? "" : formatClock(nextEnd),
    range_pages: "",
  });
  const moveHandle = (handle: "start" | "end", seconds: number) => {
    const clamped = Math.max(0, Math.min(durationS, seconds));
    if (handle === "start") save(Math.min(clamped, end - MIN_SPAN_S), end);
    else save(start, Math.max(clamped, start + MIN_SPAN_S));
  };
  const readSeconds = (clientX: number) => {
    const box = track.current?.getBoundingClientRect();
    if (!box || !box.width) return 0;
    return Math.round(((clientX - box.left) / box.width) * durationS);
  };
  const grab = (handle: "start" | "end") => (event: PointerEvent<HTMLDivElement>) => {
    event.stopPropagation();
    dragging.current = handle;
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const drag = (event: PointerEvent<HTMLDivElement>) => {
    if (!dragging.current) return;
    const seconds = readSeconds(event.clientX);
    moveHandle(dragging.current, seconds);
    onSeek(seconds);
  };
  const release = () => { dragging.current = null; };
  const nudge = (handle: "start" | "end") => (event: KeyboardEvent<HTMLDivElement>) => {
    const step = event.shiftKey ? KEY_BIG_STEP_S : KEY_STEP_S;
    const at = handle === "start" ? start : end;
    if (event.key === "ArrowLeft" || event.key === "ArrowDown") moveHandle(handle, at - step);
    else if (event.key === "ArrowRight" || event.key === "ArrowUp") moveHandle(handle, at + step);
    else if (event.key === "Home") moveHandle(handle, 0);
    else if (event.key === "End") moveHandle(handle, durationS);
    else return;
    event.preventDefault();
  };
  // a typed time refused, said once under the row, and which box it was typed in
  const [problem, setProblem] = useState<{ field: "" | "start" | "end"; text: string }>({ field: "", text: "" });
  useEffect(() => { setProblem({ field: "", text: "" }); }, [start, end]);
  const commitStart = (seconds: number | null) => {
    if (seconds === null) setProblem({ field: "start", text: "Write the start as m:ss or h:mm:ss." });
    else if (seconds >= end) setProblem({ field: "start", text: `The start must come before the end, ${formatClock(end)}.` });
    else save(seconds, end);
  };
  const commitEnd = (seconds: number | null) => {
    if (seconds === null) setProblem({ field: "end", text: "Write the end as m:ss or h:mm:ss." });
    else if (seconds <= start) setProblem({ field: "end", text: `The end must come after the start, ${formatClock(start)}.` });
    else if (seconds > durationS + 0.5) setProblem({ field: "end", text: `The recording ends at ${formatClock(durationS)}.` });
    else save(start, seconds);
  };
  const share = (seconds: number) => `${(seconds / durationS) * 100}%`;
  const frameTimes = Array.from({ length: FILMSTRIP_FRAMES }, (_, at) => ((at + 0.5) * durationS) / FILMSTRIP_FRAMES);

  return (
    <>
      <div className="range-track" ref={track} onPointerMove={drag} onPointerUp={release} onPointerCancel={release}
        onPointerDown={(event) => onSeek(readSeconds(event.clientX))} aria-label="the recording's length">
        {isAudio ? (
          <svg className="range-wave" viewBox={`0 0 ${WAVEFORM_BARS} 100`} preserveAspectRatio="none" aria-hidden="true">
            {(peaks ?? []).map((peak, at) => {
              const height = Math.max(peak * 90, 2);
              return <rect key={at} x={at + 0.15} width={0.7} y={50 - height / 2} height={height} rx={0.3} />;
            })}
          </svg>
        ) : (
          <div className="range-film" aria-hidden="true">
            {frameTimes.map((seconds) => (
              <img key={seconds} alt="" loading="lazy" draggable={false}
                src={`/api${buildPreviewPath(source, "frame", `at=${seconds.toFixed(1)}`)}`} />
            ))}
          </div>
        )}
        <div className="range-shade before" style={{ left: 0, width: share(start) }} />
        <div className="range-shade after" style={{ left: share(end), right: 0 }} />
        <div className="range-window" style={{ left: share(start), width: share(end - start) }} />
        {playerS !== null && (
          <div className="range-playhead" style={{ left: share(Math.min(playerS, durationS)) }} aria-hidden="true" />
        )}
        {(["start", "end"] as const).map((handle) => (
          <div key={handle} className={`range-handle ${handle}`} role="slider" tabIndex={0}
            aria-label={`${handle} of processing`} aria-valuemin={0} aria-valuemax={Math.round(durationS)}
            aria-valuenow={Math.round(handle === "start" ? start : end)}
            aria-valuetext={formatClock(handle === "start" ? start : end)}
            style={{ left: share(handle === "start" ? start : end) }}
            onPointerDown={grab(handle)} onKeyDown={nudge(handle)}>
            <span className="range-grip" />
          </div>
        ))}
      </div>
      <div className="range-fields">
        <div className="range-group">
          <TimeField label="Start" seconds={start} isInvalid={problem.field === "start"} onCommit={commitStart} />
          {playerS !== null && (
            <button className="btn ghost small" onClick={() => moveHandle("start", playerS)}
              title="start processing where the player stands">
              <Icon name="play" size={12} /> Start at {formatClock(playerS)}
            </button>
          )}
        </div>
        <div className="range-group">
          <TimeField label="End" seconds={end} isInvalid={problem.field === "end"} onCommit={commitEnd} />
          {playerS !== null && (
            <button className="btn ghost small" onClick={() => moveHandle("end", playerS)}
              title="end processing where the player stands">
              <Icon name="stop" size={12} /> End at {formatClock(playerS)}
            </button>
          )}
        </div>
      </div>
      {problem.text && <p className="range-error" role="alert">{problem.text}</p>}
      <p className="range-summary">
        Processing <strong>{formatClock(end - start)}</strong> of {formatClock(durationS)}
        {" "}({Math.round(((end - start) / durationS) * 100)}%)
      </p>
    </>
  );
}

function PageRange({ source, pageCount, value, onChange }: {
  source: PreviewSource;
  pageCount: number;
  value: RangeValue;
  onChange: (value: RangeValue) => void;
}) {
  const chosen = parsePages(value.range_pages);
  const first = Math.min(chosen?.[0] ?? 1, pageCount);
  const last = Math.min(chosen?.[1] ?? pageCount, pageCount);
  const [drafts, setDrafts] = useState<[string, string]>([String(first), String(last)]);
  const [problem, setProblem] = useState("");
  useEffect(() => { setDrafts([String(first), String(last)]); }, [first, last]);
  const strip = useRef<HTMLDivElement>(null);
  const save = (nextFirst: number, nextLast: number) => {
    setProblem("");
    onChange({ range_start: "", range_end: "",
      range_pages: nextFirst === 1 && nextLast === pageCount ? "" : formatPages(nextFirst, nextLast) });
  };
  const choose = (page: number, isLast: boolean) => {
    if (isLast) save(Math.min(first, page), Math.max(first, page));
    else save(page, Math.max(page, last));
  };
  const commit = () => {
    const [typedFirst, typedLast] = drafts.map(Number);
    if (!Number.isInteger(typedFirst) || !Number.isInteger(typedLast) || typedFirst < 1 || typedLast > pageCount) {
      setProblem(`The pages run from 1 to ${pageCount}.`);
      return;
    }
    if (typedLast < typedFirst) {
      setProblem("The last page must come after the first.");
      return;
    }
    save(typedFirst, typedLast);
    strip.current?.querySelector(`[data-page="${typedFirst}"]`)?.scrollIntoView?.({ block: "nearest", inline: "start" });
  };
  const pages = Array.from({ length: pageCount }, (_, at) => at + 1);

  return (
    <>
      <div className="page-strip" ref={strip} role="listbox" aria-label="the PDF's pages" aria-multiselectable="true">
        {pages.map((page) => {
          const isIn = page >= first && page <= last;
          return (
            <button key={page} data-page={page} role="option" aria-selected={isIn}
              className={`page-thumb${isIn ? " in" : ""}${page === first ? " first" : ""}${page === last ? " last" : ""}`}
              title={`page ${page}: click to start here, shift-click to end here`}
              onClick={(event) => choose(page, event.shiftKey)}>
              <img alt="" loading="lazy" draggable={false}
                src={`/api${buildPreviewPath(source, "page", `page=${page}`)}`} />
              <span>{page}</span>
            </button>
          );
        })}
      </div>
      <div className="range-fields range-group">
        <label className={`range-field${problem ? " invalid" : ""}`}>
          <span>Pages</span>
          <input type="number" min={1} max={pageCount} aria-label="first page" value={drafts[0]} aria-invalid={Boolean(problem)}
            onChange={(event) => setDrafts([event.target.value, drafts[1]])} onBlur={commit}
            onKeyDown={(event) => { if (event.key === "Enter") commit(); }} />
        </label>
        <span className="range-dash">to</span>
        <label className={`range-field${problem ? " invalid" : ""}`}>
          <input type="number" min={1} max={pageCount} aria-label="last page" value={drafts[1]} aria-invalid={Boolean(problem)}
            onChange={(event) => setDrafts([drafts[0], event.target.value])} onBlur={commit}
            onKeyDown={(event) => { if (event.key === "Enter") commit(); }} />
        </label>
        <span className="muted">of {pageCount}</span>
      </div>
      {problem && <p className="range-error" role="alert">{problem}</p>}
      <p className="range-summary">
        Processing <strong>{last - first + 1}</strong> of {pageCount} pages · click a page to start there, shift-click to
        end there
      </p>
    </>
  );
}

// `onClose` absent: the editor stands in a dialog that closes it, with no close button of its own; `playerS` null: no
// player stands beside it, so no playhead and no "Start at" buttons
export default function RangeEditor({ source, durationS, pageCount, isAudio, value, onChange, playerS = null,
  onSeek = () => undefined, onClose }: {
  source: PreviewSource;
  durationS: number;
  pageCount: number;
  isAudio: boolean;
  value: RangeValue;
  onChange: (value: RangeValue) => void;
  playerS?: number | null;
  onSeek?: (seconds: number) => void;
  onClose?: () => void;
}) {
  const isWhole = !value.range_start && !value.range_end && !value.range_pages;
  return (
    // in a dialog (no `onClose`), the dialog is its frame and its title: no card and no heading of its own
    <section className={onClose ? "card range-editor" : "range-editor in-dialog"} aria-label="the part to process">
      <header className="range-head">
        {onClose ? <h3><Icon name="range" /> Process only a part</h3> : <span />}
        <div className="range-head-actions">
          <button className="btn ghost small" disabled={isWhole} onClick={() => onChange({ range_start: "", range_end: "", range_pages: "" })}>
            <Icon name="reset" size={14} /> Whole<span className="range-reset-noun">{pageCount ? "PDF" : "recording"}</span>
          </button>
          {onClose && (
            <button className="btn ghost icon-only small" aria-label="close the range" onClick={onClose}><Icon name="x" size={14} /></button>
          )}
        </div>
      </header>
      {pageCount ? (
        <PageRange source={source} pageCount={pageCount} value={value} onChange={onChange} />
      ) : durationS > 0 ? (
        <TimeRange source={source} durationS={durationS} isAudio={isAudio} value={value} onChange={onChange}
          playerS={playerS} onSeek={onSeek} />
      ) : (
        <p className="muted">The recording's length could not be read, so no part of it can be chosen.</p>
      )}
    </section>
  );
}
