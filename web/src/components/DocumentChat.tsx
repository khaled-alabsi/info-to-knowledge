import { useState } from "react";
import { describeError, sendJson } from "../api";
import WorkerLive from "./WorkerLive";

// The chat on a run's final document, in every workflow (the owner: "a chat option in the final docuemnt in the artifact
// tab, if i want to make some new worker to change somethings in the document"): a worker reads what it wants and edits,
// adds or removes what it is asked; the version before each edit stays among the document's versions
export default function DocumentChat({ runBase, onClose }: { runBase: string; onClose: () => void }) {
  const [round, setRound] = useState(0);
  const [error, setError] = useState("");

  const startAgain = () => {
    sendJson(`${runBase}/document-chat/start-again`)
      .then(() => { setRound(round + 1); setError(""); })
      .catch((reason) => setError(describeError(reason)));
  };

  const footer = (
    <span className="chat-again">
      {error && <span className="muted">{error}</span>}
      <button type="button" className="btn ghost small" onClick={startAgain}>Start again</button>
    </span>
  );
  return (
    <WorkerLive key={round} title="Chat about this document" label="the document chat" runBase={runBase}
      emptyText="Ask about this document, or ask for a change. The worker reads what it needs and edits what you ask; the version before stays among the document's versions."
      readPath={`${runBase}/document-chat`} sendPath={`${runBase}/document-chat/messages`} isRunning canAsk chatOnly
      footer={footer} onClose={onClose} />
  );
}
