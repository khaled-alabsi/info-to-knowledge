import { useCallback, useEffect, useState } from "react";
import { describeError, fetchCompressibleVideos, formatSize, startVideoCompression } from "../api";
import type { CompressibleVideo } from "../api";
import { toggleInSet } from "../sets";
import Icon from "./Icon";

// How often the list is read again while a video is compressing or waiting
const POLL_MS = 3000;

function buildKey(video: CompressibleVideo): string {
  return `${video.root}/${video.name}`;
}

// A video can be ticked only when nothing is being done to it and it is not compressed yet
function canTick(video: CompressibleVideo): boolean {
  return video.state === "not compressed" || video.state === "failed";
}

// each state in the colour the page gives the same state of a run
const STATE_PILLS: Record<CompressibleVideo["state"], string> = {
  "not compressed": "", waiting: "queued", compressing: "running", compressed: "complete", failed: "failed" };

function describeState(video: CompressibleVideo): string {
  if (video.state === "compressing") return `compressing ${Math.round(video.progress * 100)}%`;
  if (video.state === "compressed") return `compressed · ${formatSize(video.before_bytes)} → ${formatSize(video.after_bytes)}`;
  return video.state;
}

// The Manager tab's Compress videos, in the detail view: every done run's video (no PDF, no audio) with its state, the
// ticked ones shrunk to 720p one after another, never two at once (the owner: "select one or all or none or inverse
// select ... compact them all at once"; "when you compact a video you cannot compact it again")
export default function CompressView({ onBack, onMessage }: { onBack: () => void;
  onMessage: (text: string, isError?: boolean) => void }) {
  const [videos, setVideos] = useState<CompressibleVideo[] | null>(null);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [isStarting, setIsStarting] = useState(false);

  const load = useCallback(async () => {
    try {
      setVideos(await fetchCompressibleVideos());
    } catch (problem) {
      onMessage(`The videos could not be listed: ${describeError(problem)}`, true);
    }
  }, [onMessage]);
  useEffect(() => { void load(); }, [load]);
  const isBusy = (videos ?? []).some((video) => video.state === "compressing" || video.state === "waiting");
  useEffect(() => {
    if (!isBusy) return undefined;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [isBusy, load]);

  const tickable = (videos ?? []).filter(canTick);
  const chosen = tickable.filter((video) => ticked.has(buildKey(video)));
  const chosenBytes = chosen.reduce((sum, video) => sum + video.size_bytes, 0);
  const tickAll = () => setTicked(new Set(tickable.map(buildKey)));
  const tickNone = () => setTicked(new Set());
  const invert = () => setTicked(new Set(tickable.map(buildKey).filter((key) => !ticked.has(key))));

  const start = async () => {
    setIsStarting(true);
    try {
      await startVideoCompression(chosen.map((video) => ({ root: video.root, name: video.name })));
      onMessage(`Compressing ${chosen.length} video${chosen.length === 1 ? "" : "s"}, one after another`, false);
      setTicked(new Set());
      await load();
    } catch (problem) {
      onMessage(`Not started: ${describeError(problem)}`, true);
    } finally {
      setIsStarting(false);
    }
  };

  return (
    <div className="detail manager-tool">
      <header className="detail-head">
        <button className="btn ghost icon-only back" onClick={onBack} aria-label="back"><Icon name="back" /></button>
        <div className="detail-title">
          <h2>Compress videos</h2>
          <small className="muted">Shrinks each ticked video of a done run to 720p, in place, one after another. A
            compressed video stays listed and cannot be compressed again.</small>
        </div>
      </header>
      {/* the action above the list, so it is in sight however many videos the list holds */}
      <div className="toolbar">
        <button className={`btn ${chosen.length ? "primary" : "muted-btn"}`} disabled={!chosen.length || isStarting || isBusy}
          title={isBusy ? "a compression is running: the next starts when it ends" : undefined}
          onClick={() => void start()}>
          <Icon name="gauge" /> {chosen.length ? `Compress ${chosen.length} · ${formatSize(chosenBytes)}` : "Compress"}
        </button>
        {tickable.length > 0 && (
          <div className="tick-choices" role="group" aria-label="tick">
            <button type="button" className="btn ghost small" onClick={tickAll}>All {tickable.length}</button>
            <button type="button" className="btn ghost small" onClick={tickNone}>None</button>
            <button type="button" className="btn ghost small" onClick={invert}>Invert</button>
          </div>
        )}
      </div>
      {isBusy && <p className="muted">A compression is running; the list follows it.</p>}
      {videos === null && <p className="muted">Loading…</p>}
      {videos?.length === 0 && <p className="muted">No done run has a video.</p>}
      <ul className="manager-runs">
        {(videos ?? []).map((video) => {
          const key = buildKey(video);
          return (
            <li key={key}>
              <label className={`row selectable${ticked.has(key) && canTick(video) ? " ticked" : ""}`}>
                <input type="checkbox" aria-label={`tick ${video.display_name || video.name}`} disabled={!canTick(video)}
                  checked={ticked.has(key) && canTick(video)} onChange={() => setTicked((previous) => toggleInSet(previous, key))} />
                <span className="row-main">
                  <span className="row-name" title={video.name}>{video.display_name || video.name}</span>
                  <small className="muted">{formatSize(video.size_bytes)}{video.is_in_inbox ? " · in the inbox, kept in its run folder first" : ""}</small>
                </span>
                <span className={`pill ${STATE_PILLS[video.state]}`.trim()} title={video.error || undefined}>
                  {describeState(video)}
                </span>
              </label>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
