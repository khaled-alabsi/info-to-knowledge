import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { describeError, fetchJson } from "../api";
import type { WorkerActivity } from "../api";
import Dialog from "./Dialog";
import Icon from "./Icon";
import MarkdownView from "./MarkdownView";
import PassPartMessages from "./PassPartMessages";
import WorkerTurns, { countTurns, formatClock } from "./WorkerTurns";
import type { MessageMode } from "./PassPartMessages";

// How often an open panel of a working worker asks again
const REFRESH_MS = 3000;
type TabKey = "turns" | "document" | "chat";

// One worker, live, in every workflow (the owner: "when i click, its open details of thinking, doing, live what the agent
// is doing or thinking"; "should be in all flows types"): each step it took with what the program answered, newest last,
// the thinking of the call under way as it is written, and the box to write to it
// `readPath` answers what it did, `sendPath` takes a message; `canAsk`: it holds a conversation, so it can answer in
// words; `document`: its draft, read again while it works; `linkFolder` where its figures are read from
export default function WorkerLive({ title, label, runBase, readPath, sendPath, isRunning, canAsk, document, linkFolder,
  onClose, chatOnly = false, footer, emptyText }: { title: string; label: string; runBase: string; readPath: string; sendPath: string;
  isRunning: boolean; canAsk: boolean; document?: string; linkFolder?: string; onClose: () => void; chatOnly?: boolean;
  footer?: ReactNode; emptyText?: string }) {
  const [isActivityOpen, setIsActivityOpen] = useState(false);
  const [activity, setActivity] = useState<WorkerActivity | null>(null);
  const [tab, setTab] = useState<TabKey>("turns");
  const [error, setError] = useState("");
  const [messageMode, setMessageMode] = useState<MessageMode>(chatOnly || canAsk ? "chat" : "send");

  useEffect(() => {
    let isOpen = true;
    const read = () => fetchJson<WorkerActivity>(readPath)
      .then((answer) => { if (isOpen) { setActivity(answer); setError(""); } })
      .catch((reason) => { if (isOpen) setError(describeError(reason)); });
    read();
    const timer = isRunning ? window.setInterval(read, REFRESH_MS) : undefined;
    return () => { isOpen = false; if (timer) window.clearInterval(timer); };
  }, [readPath, isRunning]);

  const speed = activity?.speed;
  const messages = activity?.messages ?? [];
  // the chat has a tab of its own (the owner: "make the chat has own tab"), its messages counted on it
  // a worker that takes no message (a fast run's one document call) has no chat
  const hasChat = !chatOnly && activity?.can_send !== false && (isRunning || messages.length > 0);
  const turnCount = countTurns(activity, isRunning);
  // the thinking has no tab of its own: each turn holds its thinking (the owner: "rename steps to turns, and make each
  // turn presist own thinking, the thinking tab is not needed")
  const tabs: { key: TabKey; label: string }[] = [
    { key: "turns", label: `Turns (${turnCount})` },
    ...(document !== undefined ? [{ key: "document" as const, label: "Document so far" }] : []),
    ...(hasChat ? [{ key: "chat" as const, label: messages.length ? `Chat (${messages.length})` : "Chat" }] : [])];
  // one live mark, in the title (the owner: "one live icon is enogh in the correct place")
  // A chat keeps reading while its panel is open, for an answer to come; its worker works only while its process lives
  const isWorking = isRunning && activity?.is_worker_running !== false;
  const heading = <>{title}{isWorking && <span className="live-mark">live</span>}</>;
  return (
    // an overlay over the page (the owner: "the live pannel should be overlay"), closed by its button or Escape; its
    // tabs go from what changes most to least (the owner: "the first one should be the active one, then the less
    // active, then the docoment so far")
    <Dialog title={heading} onClose={onClose}>
      <div className="part-live">
        {error && <p className="part-message-error">
          {/^40[45]\b/.test(error) ? "This needs the server restarted: it runs older code." : error}</p>}
        {activity?.step && <p className="muted part-speed">Step: {activity.step}</p>}
        {isWorking && activity?.doing?.what && (
          // the owner: "i want to know, if the agent now thinking or doing something else"
          <p className="part-doing"><strong>Now: {activity.doing.what}</strong>
            {activity.doing.since ? <span className="muted"> · since {formatClock(activity.doing.since)}</span> : ""}</p>
        )}
        {!chatOnly && Boolean(speed?.tokens_per_second) && speed && (isWorking || !speed.is_estimated) && (
          <p className="muted part-speed">
            {speed.is_writing ? "Writing at" : "Last call wrote at"} {speed.tokens_per_second} tokens a second
            {speed.is_estimated ? " (estimated from the text written so far)" : " (the model host's token count)"}
            {speed.prompt_tokens ? ` · its prompt ${speed.prompt_tokens.toLocaleString("en")} tokens` : ""}
          </p>
        )}
        {chatOnly && <PassPartMessages sendPath={sendPath} label={label} isRunning={isRunning} mode="chat"
          messages={messages} onSent={setActivity} emptyText={emptyText} />}
        {chatOnly && (
          <div className="chat-tools">
            <button type="button" className="btn ghost small" aria-expanded={isActivityOpen}
              onClick={() => setIsActivityOpen(!isActivityOpen)}>
              <Icon name="chevron" size={14} /> What the worker did{activity?.steps.length ? ` (${activity.steps.length})` : ""}
            </button>
            {footer}
          </div>
        )}
        {(!chatOnly || isActivityOpen) && <>
        <div className="instruction-tabs" role="tablist" aria-label={`what ${label} shows`}>
          {tabs.map((entry) => (
            <button key={entry.key} type="button" role="tab" aria-selected={tab === entry.key}
              className={tab === entry.key ? "on" : ""} onClick={() => setTab(entry.key)}>
              {entry.label}
            </button>
          ))}
        </div>
        {/* one height for every tab, its content scrolling inside it (the owner: "Each tab has its own vertical size,
            which is bad. They should have the same size or tabs with scroll if needed") */}
        <div role="tabpanel" className="part-live-panel">
          {tab === "turns" && activity && <WorkerTurns activity={activity} isRunning={isRunning} label={label} />}
          {tab === "document" && (
            <div className="part-live-document">
              {document ? <MarkdownView runBase={runBase} path={document} linkFolder={linkFolder}
                liveMs={isRunning ? REFRESH_MS : undefined} />
                : <p className="muted">No write yet in this part.</p>}
            </div>
          )}
          {tab === "chat" && <>
            {/* shown while there are messages too, since each button shows its own kind (a finished part's messages) */}
            {canAsk && (isRunning || messages.length > 0) && (
              <div className="part-message-buttons">
                <button type="button" className="btn small" aria-pressed={messageMode === "chat"}
                  onClick={() => setMessageMode("chat")}>Ask a question</button>
                <button type="button" className="btn small" aria-pressed={messageMode === "send"}
                  onClick={() => setMessageMode("send")}>Send a message</button>
              </div>
            )}
            <PassPartMessages sendPath={sendPath} label={label} isRunning={isRunning} mode={messageMode}
              messages={messages} onSent={setActivity} emptyText={emptyText} />
          </>}
        </div>
        </>}
      </div>
    </Dialog>
  );
}
