import { useCallback, useEffect, useRef, useState } from "react";
import { buildDownloadUrl, buildFileUrl, describeError, downloadZip, fetchJson, formatTime, sendJson } from "../api";
import type { ArtifactFile, ArtifactGroup, CommandOutput, FrameProposal } from "../api";
import { usePersistentState } from "../viewState";
import Dialog from "./Dialog";
import DocumentChat from "./DocumentChat";
import Icon from "./Icon";
import MarkdownView from "./MarkdownView";
import TranscriptView from "./TranscriptView";
import Spinner from "./Spinner";
import WorkerSessions from "./WorkerSessions";
import type { WorkerSession } from "./WorkerSessions";

// The group of each worker's turns: the server's group of the parts' thinking files is shown as it, under this key
const WORKERS_KEY = "thinking";
// A part's thinking file, which a part's row asks to open: opened as that part's session
const PART_THINKING = /(?:^|\/)thinking\/part-(\d+)\.md$/;

const FRAMES_PER_PAGE = 60;
// The layer that dropped a frame, as phase 2 and the Frames bar record it (`droppedBy`)
const DROPPED_BY_LABELS: Record<string, string> = {
  filter: "knowledge filter",
  redundancy: "redundancy filter",
  similarity: "similarity recheck",
  board: "board stage",
  "after-talk": "after the talk",
  "before-talk": "before the talk, no text",
  "empty-screen": "nothing but the window's bars",
  "empty-step": "step adding nothing",
  "pass-through": "shown briefly before its next step",
  "moved-copy": "the frame before, moved",
  "repeat-copy": "shown again, nothing new",
  "cut-away": "speaker between two showings of a slide",
  refine: "Refine",
  hand: "hand",
};
// While the run writes, new files appear; the list is read again this often.
const LIVE_RELOAD_MS = 5000;

// What the run produced, for reading: the document, the draft, the notes, each frame's notes, the frames, the audio with its transcript.
// An audio run whose recording the Recording tab plays leaves out the "Audio and transcript" group: the tab shows the
// same audio and the same transcript, and a video run keeps it, since there the tab plays the video.
// The file a fast-processing run's phase 3 writes: one `## Frame N · <time>` section per frame
const FRAME_DESCRIPTIONS_FILE = "03_frame-descriptions.md";

// Each frame's section of the descriptions file, by frame number: what was said while it was on screen, and its
// description, without the heading the dialog's title already gives.
export function splitFrameDescriptions(text: string): Map<number, string> {
  const sections = new Map<number, string>();
  const parts = text.split(/^## Frame (\d+) · [^\n]*$/m);
  for (let index = 1; index + 1 < parts.length; index += 2) {
    sections.set(Number(parts[index]), parts[index + 1].trim());
  }
  return sections;
}

// `frame-045.jpg` -> 45; null for a name with no frame number
export function readFrameNumber(name: string): number | null {
  const found = name.match(/frame-(\d+)\./);
  return found ? Number(found[1]) : null;
}

// A file in the reader's list: a version of the document by what it is first ("Original", "Knowledge draft after part
// 1 of 5"), its path after it; any other file by its path (the owner: three paths told nothing of which was which)
export function describeFileOption(item: ArtifactFile): string {
  if (!item.label) return item.path;
  return `${item.label.charAt(0).toUpperCase()}${item.label.slice(1)} · ${item.path}`;
}

// The server's groups with its thinking files replaced by Worker turns, the page's own group of every worker's session;
// left out while no worker ran
function buildShownGroups(serverGroups: ArtifactGroup[] | null, sessions: WorkerSession[]): ArtifactGroup[] | null {
  if (!serverGroups) return null;
  const others = serverGroups.filter((candidate) => candidate.key !== WORKERS_KEY);
  if (!sessions.length) return others;
  const at = serverGroups.findIndex((candidate) => candidate.key === WORKERS_KEY);
  const workers: ArtifactGroup = { key: WORKERS_KEY, title: "Worker turns", kind: "workers", items: [] };
  const place = at >= 0 ? at : Math.min(1, others.length);

  return [...others.slice(0, place), workers, ...others.slice(place)];
}

export default function ArtifactsView({ runBase, isLive, hasRecording, isAudio = false, onPlayFrom, openPath, onOpened,
  readPath = "", onRead, linkFolders = {}, canChat = false, workerSessions = [] }: {
  runBase: string;
  // every worker that ran or runs, a phase's or a phase 7 part's, for the Worker turns group
  workerSessions?: WorkerSession[];
  isLive: boolean;
  // the run has a final document and no run writes it, so the chat on it is offered
  canChat?: boolean;
  hasRecording: boolean;
  isAudio?: boolean;
  onPlayFrom: (seconds: number) => void;
  // One file another card asked to be read, by its path in the run folder; cleared through onOpened once it is shown.
  openPath?: string;
  onOpened?: () => void;
  // a markdown file to open full screen once it is shown; cleared through onRead
  readPath?: string;
  onRead?: () => void;
  // a markdown file's path to the folder its relative links are read from, when not its own: the document's earlier
  // versions, kept in phase 7's folder, link the published folder's figures
  linkFolders?: Record<string, string>;
}) {
  const [serverGroups, setGroups] = useState<ArtifactGroup[] | null>(null);
  // the session a part's row asked to open in Worker turns
  const [chosenSession, setChosenSession] = useState("");
  const [groupKey, setGroupKey] = usePersistentState("artifactGroup", "");
  const [filePath, setFilePath] = usePersistentState("artifactFile", "");
  const [frameIndex, setFrameIndex] = useState<number | null>(null);
  // A fast-processing run's frame descriptions by frame number, read once when a frame is first opened
  const [descriptions, setDescriptions] = useState<Map<number, string> | null>(null);
  const [framesShown, setFramesShown] = useState(FRAMES_PER_PAGE);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [isChatOpen, setIsChatOpen] = useState(false);
  // raised when the chat closes, so the reader reads the document its worker changed
  const [chatRound, setChatRound] = useState(0);
  const [isShowingHidden, setIsShowingHidden] = useState(false);
  const [imageNote, setImageNote] = useState("");
  // a failed action's note is written as an error, a progress or result note in grey
  const [isImageNoteError, setIsImageNoteError] = useState(false);
  const showNote = (text: string, isError = false) => {
    setImageNote(text);
    setIsImageNoteError(isError);
  };
  // Refine's judgement of the frames phase 3 has not read, by path; null while no proposal is open
  const [proposals, setProposals] = useState<Map<string, FrameProposal> | null>(null);
  const [isRefining, setIsRefining] = useState(false);
  const [isConfirmingDrop, setIsConfirmingDrop] = useState(false);

  const reload = useCallback(() => {
    fetchJson<ArtifactGroup[]>(`${runBase}/artifacts`)
      .then((next) => {
        setGroups(isAudio && hasRecording ? next.filter((candidate) => candidate.key !== "audio") : next);
        setError("");
      })
      .catch((reason) => setError(String(reason.message)));
  }, [runBase, isAudio, hasRecording]);

  useEffect(reload, [reload]);

  // Worker turns stands where the server's thinking files stood, or after the first group, while any worker ran (the
  // owner: "i want this to be renamed to worker turns")
  const groups = buildShownGroups(serverGroups, workerSessions);

  useEffect(() => {
    if (!isLive) return;
    const timer = window.setInterval(reload, LIVE_RELOAD_MS);
    return () => window.clearInterval(timer);
  }, [isLive, reload]);

  // A file another card asked for, shown as soon as the groups are loaded: the group that holds it, then the file.
  useEffect(() => {
    if (!openPath || !groups) return;
    const part = PART_THINKING.exec(openPath);
    const holding = groups.find((candidate) => candidate.items.some((item) => item.path === openPath));
    if (part) {
      setGroupKey(WORKERS_KEY);
      setChosenSession(`part-${Number(part[1])}`);
    } else if (holding) {
      setGroupKey(holding.key);
      setFilePath(openPath);
    }
    onOpened?.();
  }, [openPath, groups, setGroupKey, setFilePath, onOpened]);

  const group = groups?.find((candidate) => candidate.key === groupKey) ?? groups?.[0];
  const file = group?.items.find((item) => item.path === filePath) ?? group?.items[0];
  // Where the open file stands in its group, for the previous and next buttons beside the list
  const fileIndex = file && group ? group.items.indexOf(file) : -1;
  // The images shown: hidden ones only while "Show hidden" is on. The large view walks the same list.
  const frames = group?.kind === "images"
    ? group.items.filter((item) => isShowingHidden || !item.is_hidden) : [];
  const hiddenCount = group?.kind === "images" ? group.items.filter((item) => item.is_hidden).length : 0;
  const shownSelected = frames.filter((item) => selected.has(item.path));
  const isAllShownSelected = frames.length > 0 && shownSelected.length === frames.length;

  const toggleSelected = (path: string) =>
    setSelected((previous) => {
      const next = new Set(previous);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });

  const zipImages = async (paths: string[]) => {
    showNote(`building a zip of ${paths.length} image(s)…`);
    try {
      await downloadZip(runBase, paths, group?.key ?? "images");
      showNote("");
    } catch (reason) {
      showNote(describeError(reason), true);
    }
  };

  // Hiding is a display setting kept by the server; the files stay in the run folder.
  const setHidden = async (paths: string[], isHidden: boolean) => {
    try {
      await sendJson(`${runBase}/hidden`, { paths, is_hidden: isHidden });
      setSelected(new Set());
      showNote(`${isHidden ? "hid" : "showed again"} ${paths.length} image(s)`);
      reload();
    } catch (reason) {
      showNote(describeError(reason), true);
    }
  };

  // Refine: the classifier judges the frames phase 3 has not read, and the ones it would drop are ticked. Nothing changes
  // until Drop selected: the person unticks a frame it should keep, ticks one it missed, or rejects the proposal.
  const refine = async () => {
    setIsRefining(true);
    showNote("the frame knowledge classifier is judging the frames phase 3 has not read…");
    try {
      const answer = await sendJson<CommandOutput & { records?: FrameProposal[] }>(`${runBase}/frames/refine`, {});
      if (answer.exit_code !== 0) {
        showNote(answer.output.split("\n")[0], true);
        return;
      }
      const judged = new Map((answer.records ?? []).map((record) => [record.path, record] as const));
      const proposed = [...judged.values()].filter((record) => record.decision === "drop");
      setProposals(judged);
      setSelected(new Set(proposed.map((record) => record.path)));
      showNote(`Refine proposes ${proposed.length} of ${judged.size} frame(s) phase 3 has not read: untick a frame to `
        + "keep it, tick one to drop it too, then Drop selected");
    } catch (reason) {
      showNote(describeError(reason), true);
    } finally {
      setIsRefining(false);
    }
  };

  const rejectProposal = () => {
    setProposals(null);
    setSelected(new Set());
    showNote("proposal rejected; nothing was dropped");
  };

  // Accepting a proposal compares the ticks with it: a proposed frame left unticked is an FP, a ticked frame it did
  // not propose an FN; the server saves both as learning data for the classifier
  const proposedPaths = new Set([...(proposals?.values() ?? [])].filter((record) => record.decision === "drop")
    .map((record) => record.path));
  const falseDropCount = [...proposedPaths].filter((path) => !selected.has(path)).length;
  const missedDropCount = shownSelected.filter((item) => !proposedPaths.has(item.path)).length;

  const dropSelected = async () => {
    setIsConfirmingDrop(false);
    const slugOf = (path: string) => path.split("/").pop()!.replace(/\.\w+$/, "");
    const judged = [...(proposals?.values() ?? [])];
    try {
      const answer = await sendJson<CommandOutput>(`${runBase}/frames/drop`, {
        frames: shownSelected.map((item) => slugOf(item.path)),
        proposed: judged.filter((record) => record.decision === "drop").map((record) => record.slug),
        drop_scores: Object.fromEntries(judged.map((record) => [record.slug, record.dropScore])),
      });
      // The summary lines, never the list of frames under them
      showNote(answer.output.split("\n").filter((line) => !line.startsWith("  ")).join(" · "), answer.exit_code !== 0);
      if (answer.exit_code === 0) {
        setProposals(null);
        setSelected(new Set());
        reload();
      }
    } catch (reason) {
      showNote(describeError(reason), true);
    }
  };
  // A PDF's frames are its pages or its figures: each is shown by its page, and the video's tools that drop frames have
  // nothing to do there (the owner: "what is frames in pdf?")
  const isPdfGroup = Boolean(group?.items.some((item) => typeof item.page === "number"));
  const isFramesGroup = group?.key === "frames" && !isPdfGroup;

  const hasDescriptions = Boolean(groups?.some((candidate) => candidate.key === "descriptions"));
  useEffect(() => {
    if (frameIndex === null || descriptions !== null || !hasDescriptions) return;
    fetch(buildFileUrl(runBase, FRAME_DESCRIPTIONS_FILE))
      .then((response) => (response.ok ? response.text() : ""))
      .then((text) => setDescriptions(splitFrameDescriptions(text)))
      .catch(() => setDescriptions(new Map()));
  }, [frameIndex, descriptions, hasDescriptions, runBase]);

  useEffect(() => {
    if (frameIndex === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "ArrowRight") setFrameIndex((index) => Math.min((index ?? 0) + 1, frames.length - 1));
      if (event.key === "ArrowLeft") setFrameIndex((index) => Math.max((index ?? 0) - 1, 0));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [frameIndex, frames.length]);

  // "Play from" a frame: the detail view opens its Recording tab and starts the player at that frame's second.
  const playFrom = (seconds: number) => {
    setFrameIndex(null);
    onPlayFrom(seconds);
  };

  if (error) return <p className="error">{error}</p>;
  if (!groups) return <Spinner />;
  if (!groups.length) return <p className="muted">This run has produced nothing to look at yet.</p>;

  return (
    <div className="artifacts">
      <div className="artifact-bar">
        <div className="chips" role="tablist">
          {groups.map((candidate) => (
            <button key={candidate.key} role="tab" aria-selected={candidate.key === group?.key}
              className={`chip${candidate.key === group?.key ? " on" : ""}`}
              onClick={() => {
                setGroupKey(candidate.key);
                setFilePath("");
                setFramesShown(FRAMES_PER_PAGE);
                setSelected(new Set());
                showNote("");
              }}>
              {candidate.title} <small>{candidate.kind === "workers" ? workerSessions.length : candidate.items.length}</small>
            </button>
          ))}
        </div>
        {isLive && <small className="muted live-note"><span className="dot running" /> updating while the run writes</small>}
      </div>

      {group?.kind === "markdown" && file && (
        <>
          {group.items.length > 1 && (
            <div className="file-picker">
              <button className="btn small" aria-label="previous file" title="previous file"
                disabled={fileIndex <= 0} onClick={() => setFilePath(group.items[fileIndex - 1].path)}>‹ Previous</button>
              <select className="file-select" value={file.path} onChange={(event) => setFilePath(event.target.value)}>
                {group.items.map((item) => <option key={item.path} value={item.path}>{describeFileOption(item)}</option>)}
              </select>
              <button className="btn small" aria-label="next file" title="next file"
                disabled={fileIndex >= group.items.length - 1}
                onClick={() => setFilePath(group.items[fileIndex + 1].path)}>Next ›</button>
            </div>
          )}
          {canChat && group.key === "document" && (
            <div className="document-chat-open">
              <button className="btn small" onClick={() => setIsChatOpen(true)}>Chat about this document</button>
            </div>
          )}
          {isChatOpen && <DocumentChat runBase={runBase} onClose={() => { setIsChatOpen(false); setChatRound(chatRound + 1); }} />}
          <MarkdownView key={chatRound} runBase={runBase} path={file.path} linkFolder={linkFolders[file.path]} isReadingAsked={readPath!== "" && readPath === file.path}
            onReadingStarted={onRead} />
        </>
      )}

      {group?.kind === "images" && (
        <>
          <div className="image-tools">
            <label className="check">
              <input type="checkbox" checked={isAllShownSelected} disabled={!frames.length}
                aria-label="select every image shown"
                onChange={() => setSelected(isAllShownSelected ? new Set() : new Set(frames.map((item) => item.path)))} />
              <span>{shownSelected.length ? `${shownSelected.length} selected` : `${frames.length} shown`}</span>
            </label>
            <button className="btn small" disabled={!shownSelected.length}
              onClick={() => zipImages(shownSelected.map((item) => item.path))}>
              <Icon name="download" size={14} /> Download selected
            </button>
            <button className="btn small" disabled={!frames.length} onClick={() => zipImages(frames.map((item) => item.path))}>
              <Icon name="download" size={14} /> Download all ({frames.length})
            </button>
            {shownSelected.some((item) => !item.is_hidden) && (
              <button className="btn small" onClick={() => setHidden(shownSelected.filter((item) => !item.is_hidden).map((item) => item.path), true)}
                title="hide them from this view for good; the files stay in the run folder">
                Hide selected
              </button>
            )}
            {shownSelected.some((item) => item.is_hidden) && (
              <button className="btn small" onClick={() => setHidden(shownSelected.filter((item) => item.is_hidden).map((item) => item.path), false)}>
                Show selected again
              </button>
            )}
            {group?.key === "cuts" && (
              <button className="btn small" disabled={isLive}
                title={isLive ? "not while the run writes: stop it or wait for it to end"
                  : "move the cuts the document never placed into the run folder's .trash/cuts; nothing is deleted"}
                onClick={async () => {
                  try {
                    const answer = await sendJson<CommandOutput>(`${runBase}/cuts/tidy`);
                    showNote(answer.output.split("\n")[0], answer.exit_code !== 0);
                    reload();
                  } catch (reason) {
                    showNote(describeError(reason), true);
                  }
                }}>
                Set aside unused cuts
              </button>
            )}
            {hiddenCount > 0 && (
              <label className="switch small-switch">
                <input type="checkbox" checked={isShowingHidden} onChange={(event) => setIsShowingHidden(event.target.checked)} />
                <span className="track" />
                <span>Show hidden ({hiddenCount})</span>
              </label>
            )}
            {isFramesGroup && (
              <button className="btn small" disabled={isLive || isRefining} onClick={refine}
                title={isLive ? "not while the run writes: stop it or wait for it to end"
                  : "the frame knowledge classifier proposes which frames phase 3 has not read hold no knowledge"}>
                <span className={isRefining ? "spinning" : undefined}><Icon name="refresh" size={14} /></span> Refine
              </button>
            )}
            {isFramesGroup && (
              <button className="btn small danger" disabled={isLive || (!proposals && !shownSelected.length)}
                onClick={() => setIsConfirmingDrop(true)}
                title={isLive ? "not while the run writes: stop it or wait for it to end"
                  : proposals ? "drop the ticked frames; what you changed in the proposal is saved as learning data"
                  : "take them out of the run before phase 3 reads them; they move to Dropped frames"}>
                {proposals ? `Accept proposal (${shownSelected.length})`
                  : `Drop selected${shownSelected.length ? ` (${shownSelected.length})` : ""}`}
              </button>
            )}
            {isFramesGroup && proposals && (
              <button className="btn small" onClick={rejectProposal}>Reject proposal</button>
            )}
            {imageNote && <small className={isImageNoteError ? "error" : "muted"}>{imageNote}</small>}
          </div>
          <div className="thumbs">
            {frames.slice(0, framesShown).map((item, index) => (
              <div key={item.path} className={`thumb-wrap${selected.has(item.path) ? " selected" : ""}${item.is_hidden ? " is-hidden" : ""}`}>
                <input type="checkbox" className="thumb-check" checked={selected.has(item.path)}
                  aria-label={`select ${item.name}`} onChange={() => toggleSelected(item.path)} />
                {item.is_hidden && <span className="hidden-badge">hidden</span>}
                {item.tag && !(isFramesGroup && item.tag === "other") && (
                  <span className={`tag-badge tag-${item.tag}`} title="the frame classifier's tag">{item.tag}</span>
                )}
                {isFramesGroup && proposals?.get(item.path)?.decision === "drop" && (
                  <span className="proposed-badge" title="Refine proposes to drop this frame">
                    proposed · {proposals.get(item.path)!.dropScore.toFixed(2)}
                  </span>
                )}
                <button className="thumb" onClick={() => setFrameIndex(index)} title={item.name}>
                  <img src={buildFileUrl(runBase, item.path)} alt={item.name} loading="lazy" />
                  <span>
                    {item.name.replace(/\.\w+$/, "")}
                    {typeof item.page === "number" ? <em> · page {item.page}</em>
                      : typeof item.time_s === "number" && <em> · {formatTime(item.time_s)}</em>}
                    {typeof item.drop_score === "number" && <em title="the frame classifier's people score: dropped at or above its threshold"> · drop score {item.drop_score.toFixed(2)}</em>}
                    {item.dropped_by && (
                      <em className="dropped-by" title={item.drop_reason || undefined}>
                        {" · dropped by "}{DROPPED_BY_LABELS[item.dropped_by] ?? item.dropped_by}
                        {item.drop_class ? ` (${item.drop_class})` : ""}
                        {item.covered_by?.length ? `: held by ${item.covered_by.join(", ")}` : ""}
                      </em>
                    )}
                  </span>
                </button>
              </div>
            ))}
          </div>
          {framesShown < frames.length && (
            <button className="btn block" onClick={() => setFramesShown(framesShown + FRAMES_PER_PAGE)}>
              Show more ({frames.length - framesShown} left)
            </button>
          )}
        </>
      )}

      {group?.kind === "workers" && <WorkerSessions sessions={workerSessions} chosenKey={chosenSession} />}
      {group?.kind === "audio" && <AudioWithTranscript runBase={runBase} items={group.items} />}
      {group?.kind === "text" && <TranscriptView runBase={runBase} isPdf playingS={0} onPlayFrom={() => {}} />}

      {isConfirmingDrop && (
        <Dialog title={proposals ? "Accept proposal" : "Drop frames"} onClose={() => setIsConfirmingDrop(false)}>
          <p className="muted">
            {shownSelected.length} frame(s) leave the run before phase 3 reads them: they move to Dropped frames, and what
            was said while each was on screen goes to the frame before it.
          </p>
          {proposals && (
            <p className="muted">
              {falseDropCount || missedDropCount
                ? `Learning data for the classifier: ${falseDropCount} FP (proposed, you keep them) and ${missedDropCount} `
                  + "FN (you drop them, not proposed) are saved."
                : "You accept the proposal as it stands: no learning data is saved."}
            </p>
          )}
          <pre className="listing">{shownSelected.map((item) => item.name).join("\n")}</pre>
          <div className="dialog-actions">
            <button className="btn ghost" onClick={() => setIsConfirmingDrop(false)}>Cancel</button>
            <button className="btn danger-solid" onClick={dropSelected}>
              {proposals ? "Accept" : `Drop ${shownSelected.length}`}
            </button>
          </div>
        </Dialog>
      )}
      {frameIndex !== null && frames[frameIndex] && (
        <Dialog title={`${frames[frameIndex].name}${typeof frames[frameIndex].page === "number" ? ` · page ${frames[frameIndex].page}`
          : typeof frames[frameIndex].time_s === "number" ? ` · at ${formatTime(frames[frameIndex].time_s as number)}` : ""} · ${frameIndex + 1} of ${frames.length}`} onClose={() => setFrameIndex(null)}>
          <div className="viewer">
            <img src={buildFileUrl(runBase, frames[frameIndex].path)} alt={frames[frameIndex].name} />
            <div className="viewer-nav">
              <button className="btn" disabled={frameIndex === 0} onClick={() => setFrameIndex(frameIndex - 1)}>
                <Icon name="back" /> Previous
              </button>
              {hasRecording && typeof frames[frameIndex].page !== "number" && typeof frames[frameIndex].time_s === "number" && (
                <button className="btn primary" onClick={() => playFrom(frames[frameIndex].time_s as number)}>
                  <Icon name="play" /> Play from {formatTime(frames[frameIndex].time_s as number)}
                </button>
              )}
              <a className="btn ghost" href={buildDownloadUrl(runBase, frames[frameIndex].path)}>
                <Icon name="download" /> Download
              </a>
              {(() => {
                // the frame's own notes, from the Frame notes group, read in place of the frames
                const number = readFrameNumber(frames[frameIndex].name);
                const notes = groups?.find((candidate) => candidate.key === "processed")?.items
                  .find((item) => number !== null && readFrameNumber(item.name) === number);
                return notes ? (
                  <button className="btn" onClick={() => {
                    setGroupKey("processed");
                    setFilePath(notes.path);
                    setFrameIndex(null);
                  }}><Icon name="eye" /> Read its notes</button>
                ) : null;
              })()}
              <button className="btn" disabled={frameIndex === frames.length - 1}
                onClick={() => setFrameIndex(frameIndex + 1)}>
                Next <span className="flip"><Icon name="back" /></span>
              </button>
            </div>
            {(() => {
              const number = readFrameNumber(frames[frameIndex].name);
              const description = number === null ? undefined : descriptions?.get(number);
              return description ? (
                <div className="frame-description" aria-label="frame description">{description}</div>
              ) : null;
            })()}
          </div>
        </Dialog>
      )}
    </div>
  );
}

// Phase 1's audio, and its transcript under it: a line's time, or a line found by the search, plays the audio from there.
function AudioWithTranscript({ runBase, items }: { runBase: string; items: ArtifactGroup["items"] }) {
  const player = useRef<HTMLAudioElement>(null);
  const [playingS, setPlayingS] = useState(0);

  const playFrom = (seconds: number) => {
    const audio = player.current;
    if (!audio) return;
    audio.currentTime = seconds;
    audio.play().catch(() => {});
  };

  return (
    <>
      {items.map((item, at) => (
        <div key={item.path} className="card audio">
          <div className="audio-head">
            <strong>{item.name}</strong>
            <a className="btn ghost small" href={buildDownloadUrl(runBase, item.path)}><Icon name="download" size={14} /> Download</a>
          </div>
          <audio ref={at === 0 ? player : undefined} controls preload="metadata" src={buildFileUrl(runBase, item.path)}
            onTimeUpdate={(event) => setPlayingS(event.currentTarget.currentTime)} />
        </div>
      ))}
      <TranscriptView runBase={runBase} playingS={playingS} onPlayFrom={playFrom} />
    </>
  );
}
