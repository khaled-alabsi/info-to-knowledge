import { useEffect, useRef, useState } from "react";
import { describeError, sendJson } from "../api";
import type { PassPartMessage, WorkerActivity } from "../api";
import Icon from "./Icon";

export type MessageMode = "chat" | "send";

// A worker's answer is markdown: its bold is shown bold, never as stars
function showBold(text: string) {
  return text.split(/\*\*(.+?)\*\*/g).map((piece, index) => (index % 2 ? <strong key={index}>{piece}</strong> : piece));
}

function describeState(message: PassPartMessage, isRunning: boolean): string {
  if (!message.is_sent) return isRunning ? "waiting for its next call" : "not sent: the worker is not running";
  if (!message.is_question) return "the worker has it";

  return message.answer ? "" : "sent, no answer yet";
}

// A server started before this feature has no route for it, and answers 404 or 405 (the owner: "i clicked ask, nothing
// is changed visualy")
function describeSendError(error: string): string {
  return /^40[45]\b/.test(error)
    ? "Not sent: the server runs older code and cannot take this yet. Restart the server, then send it again."
    : `Not sent: ${error}`;
}

// What the person writes to a running worker (the owner: "chat with worker while it works", "send a message while it
// worke as selected role"): a question it answers in words, or a message as user or system. Only its closing step ends
// its work, so an answer in words never does; each reaches the worker before its next call
export default function PassPartMessages({ sendPath, label, isRunning, mode, messages, onSent,
  emptyText = "Ask the worker what it is doing, or why." }: { sendPath: string;
  label: string; isRunning: boolean; mode: MessageMode; messages: PassPartMessage[];
  onSent: (activity: WorkerActivity) => void; emptyText?: string }) {
  const [text, setText] = useState("");
  const [role, setRole] = useState<"user" | "system">("user");
  const [error, setError] = useState("");
  const [isSending, setIsSending] = useState(false);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const listRef = useRef<HTMLOListElement>(null);
  const isChat = mode === "chat";
  // each mode shows its own kind: Ask a question a chat of questions and answers, Send a message only the messages
  // added, which get no reply (the owner: "in one it should look like chat and the other ... append message only")
  const shown = messages.filter((message) => message.is_question === isChat);

  const send = () => {
    setIsSending(true);
    sendJson<WorkerActivity>(sendPath, { text, role: isChat ? "user" : role, is_question: isChat })
      .then((activity) => { onSent(activity); setText(""); setError(""); })
      .catch((reason) => setError(describeSendError(describeError(reason))))
      .finally(() => setIsSending(false));
  };

  // The box grows with its text, and the newest message stays in sight
  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    // Empty it keeps its one row: measured as the dialog opens, its height came out a few pixels and cut the placeholder
    box.style.height = "auto";
    if (text) box.style.height = `${box.scrollHeight}px`;
  }, [text]);
  useEffect(() => {
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [shown.length, shown[shown.length - 1]?.answer]);

  const sendLabel = isSending ? "Sending" : isChat ? "Ask" : "Send";
  return (
    <div className="chat">
      {shown.length > 0 ? (
        <ol ref={listRef} className="chat-list" aria-label={`messages to ${label}`}>
          {shown.map((message, index) => message.is_question ? (
            <li key={index}>
              <div className="chat-bubble you">{message.text}</div>
              {describeState(message, isRunning) && <div className="chat-state">{describeState(message, isRunning)}</div>}
              {message.answer && <div className="chat-bubble worker">{showBold(message.answer)}</div>}
            </li>
          ) : (
            // a message expects no answer, so it is a note across the chat, never a bubble waiting for a reply (the
            // owner: "i choosed send a message and not open a chat, now it look as i expect an answere")
            <li key={index} className="chat-note">
              <div className="chat-note-head">Message as {message.role} · {describeState(message, isRunning)}</div>
              {message.text}
            </li>
          ))}
        </ol>
      ) : (
        <p className="chat-empty">{isChat ? emptyText : "No message added yet. The worker reads each one and gives no reply."}</p>
      )}
      {error && <p className="part-message-error" role="alert">{error}</p>}
      {isRunning && (
        <>
          <div className="chat-composer">
            <textarea ref={boxRef} aria-label={isChat ? "question to the worker" : "message to the worker"} value={text} rows={1}
              placeholder={isChat ? "Ask the worker something" : "Write to the worker"}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
                event.preventDefault();
                if (!isSending && text.trim()) send();
              }} />
            <button type="button" className="chat-send" aria-label={sendLabel} title={sendLabel}
              disabled={isSending || !text.trim()} onClick={send}><Icon name="arrowUp" size={16} /></button>
          </div>
          <div className="chat-hint">
            {!isChat && (
              <label>Send as{" "}
                <select value={role} onChange={(event) => setRole(event.target.value as "user" | "system")}>
                  <option value="user">user</option>
                  <option value="system">system</option>
                </select>
              </label>
            )}
            <span>
              Enter sends, Shift+Enter adds a line. The worker reads it before its next call.
              {!isChat && role === "system" ? " As system it joins the worker's instructions, and the model reads its whole prompt again, which takes minutes." : ""}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
