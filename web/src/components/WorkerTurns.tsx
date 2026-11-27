import type { PassPartMessage, WorkerActivity, WorkerToolStep, WorkerCallTurn } from "../api";
import Icon from "./Icon";
import { MarkdownText } from "./WorkerView";

// A worker with no saved turns (a phase's call steps) has each step as a turn of its own; only its newest thinking is
// kept, which goes with its newest turn unless that thinking is the call still under way
function buildTurns(activity: WorkerActivity, isTurnUnderWay: boolean): WorkerCallTurn[] {
  if (activity.turns) return activity.turns;
  const turns: WorkerCallTurn[] = [];
  activity.steps.forEach((step) => {
    const last = turns[turns.length - 1];
    if (last && last.call === step.call) last.steps.push(step);
    else turns.push({ at: step.at, call: step.call, thinking: "", text: "", steps: [step] });
  });
  if (!isTurnUnderWay && activity.thinking && turns.length) turns[turns.length - 1].thinking = activity.thinking;

  return turns;
}

function countWords(text: string): number {
  return text.trim() ? text.trim().split(/\s+/).length : 0;
}

// A tool's icon by what it does: reads look, writes edit, a close checks, a report warns
function pickToolIcon(tool: string): string {
  if (/^(read|search|look|list|find)/.test(tool)) return "eye";
  if (/^(write|edit|replace|add|move|merge|split|rename|remove|drop|keep)/.test(tool)) return "pencil";
  if (/^(finish|submit|close)/.test(tool)) return "check";
  if (/report/.test(tool)) return "alert";

  return "terminal";
}

// "read_knowledge_outline" as "Read knowledge outline"
function nameTool(tool: string): string {
  const words = tool.replace(/_/g, " ");

  return words.charAt(0).toUpperCase() + words.slice(1);
}

// A value as text: a list one item a line, an item of several fields its values joined
function describeValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(describeValue).join("\n");
  if (value && typeof value === "object") return Object.values(value).map(describeValue).filter(Boolean).join(" · ");

  return value === null || value === undefined ? "" : String(value);
}

// The call's inputs as name and value, never raw JSON; inputs cut short by the server stay as the text they are
function readArgs(args: string): [string, string][] | null {
  try {
    const parsed: unknown = JSON.parse(args || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    return Object.entries(parsed as Record<string, unknown>).map(([key, value]) => [key.replace(/_/g, " "),
      describeValue(value)]);
  } catch {
    return null;
  }
}

// One tool call (the owner: "tool call ... should be handled from ui as tool call (text + icon)"): its icon and name,
// its inputs as name and value, and the program's answer, its first line shown and the rest folded; a refused one marked
function ToolCall({ step }: { step: WorkerToolStep }) {
  const [firstLine, ...rest] = step.result.split("\n");
  const isRefused = /Refused/.test(step.result);
  const args = readArgs(step.args);
  return (
    <li className={`tool-call${isRefused ? " refused" : ""}`}>
      <div className="tool-call-head">
        <Icon name={pickToolIcon(step.tool)} size={14} />
        <strong>{nameTool(step.tool)}</strong>
        {step.summary && <span className="muted"> · {step.summary}</span>}
      </div>
      {args && args.length > 0 && (
        <dl className="tool-call-args">
          {args.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{value}</dd></div>)}
        </dl>
      )}
      {!args && step.args && <details><summary>inputs</summary><pre>{step.args}</pre></details>}
      {firstLine && <div className="tool-call-result">{isRefused ? "Refused: " : "→ "}{firstLine}</div>}
      {rest.length > 0 && <details><summary>whole answer</summary><pre>{rest.join("\n")}</pre></details>}
    </li>
  );
}

// A file's time comes in seconds, a conversation log's as an ISO text
export function formatClock(at: number | string | null): string {
  return at ? new Date(typeof at === "number" ? at * 1000 : at).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "";
}

// The thinking of the call under way is the next turn's, shown open while it is written
export function isTurnUnderWay(activity: WorkerActivity | null, isRunning: boolean): boolean {
  return isRunning && activity?.is_worker_running !== false && Boolean(activity?.thinking)
    && activity?.doing?.what !== "the program runs its tools";
}

// How many turns the worker took, the one under way counted
export function countTurns(activity: WorkerActivity | null, isRunning: boolean): number {
  if (!activity) return 0;
  const isUnderWay = isTurnUnderWay(activity, isRunning);

  return buildTurns(activity, isUnderWay).length + (isUnderWay ? 1 : 0);
}

function readSeconds(at: number | string | null): number | null {
  if (at === null) return null;

  return typeof at === "number" ? at : Date.parse(at) / 1000;
}

// The person's messages each turn took: a message taken before a call reaches the first turn written after it, or the
// turn under way when none is written yet; one taken before its time was kept marks no turn
function listTurnMessages(activity: WorkerActivity, turns: WorkerCallTurn[]): Map<number, PassPartMessage[]> {
  const marks = new Map<number, PassPartMessage[]>();
  for (const message of activity.messages ?? []) {
    if (!message.is_sent || !message.sent_at) continue;
    const found = turns.findIndex((turn) => (readSeconds(turn.at) ?? 0) >= message.sent_at!);
    const index = found === -1 ? turns.length : found;
    marks.set(index, [...(marks.get(index) ?? []), message]);
  }

  return marks;
}

// A line at the top of a turn naming each message of the person it took (the owner: "the turn should have some visiual
// marker, that show that a user message is recived in the turn")
function TurnMessages({ messages }: { messages: PassPartMessage[] | undefined }) {
  if (!messages?.length) return null;
  return (
    <ul className="turn-messages" aria-label="your messages this turn took">
      {messages.map((message, index) => (
        <li key={index}><Icon name="inbox" size={14} /> {message.is_question ? "Your question" : `Your message as ${message.role}`}
          {" "}reached the worker here: <span className="turn-message-text">{message.text}</span></li>
      ))}
    </ul>
  );
}

// A worker's turns, newest first (the owner: "i want the turns from newer to older sorted"): the turn under way on top
// with its thinking as it is written, then each turn with its thinking (folded), its words and its tool calls (the
// owner: "rename steps to turns, and make each turn presist own thinking"); a turn that took a message of the person
// says so at its top
export default function WorkerTurns({ activity, isRunning, label }: { activity: WorkerActivity; isRunning: boolean;
  label: string }) {
  const isUnderWay = isTurnUnderWay(activity, isRunning);
  const turns = buildTurns(activity, isUnderWay);
  if (!turns.length && !isUnderWay) {
    return <p className="muted">{isRunning ? "No turn yet: the worker reads its prompt." : "No turn saved."}</p>;
  }
  const turnMessages = listTurnMessages(activity, turns);
  const newestFirst = turns.map((turn, index) => ({ turn, index })).reverse();
  return (
    <ol className="part-turns" aria-label={`turns of ${label}`}>
      {isUnderWay && activity.thinking && (
        <li className="turn-now">
          <div className="turn-head">
            <strong>Turn {turns.length + 1}</strong> <span className="muted">now, {activity.doing?.what || "thinking"}</span>
          </div>
          <TurnMessages messages={turnMessages.get(turns.length)} />
          <details className="turn-section turn-thinking" open>
            <summary>Thinking · {countWords(activity.thinking).toLocaleString("en")} words so far</summary>
            <div aria-label={`thinking of ${label}`}><MarkdownText text={activity.thinking} isRaw={false} /></div>
          </details>
        </li>
      )}
      {newestFirst.map(({ turn, index }) => (
        <li key={index}>
          <div className="turn-head">
            <strong>Turn {index + 1}</strong> <span className="muted">{formatClock(turn.at)}</span>
          </div>
          <TurnMessages messages={turnMessages.get(index)} />
          {/* three sections, each only when the turn has it (the owner): its thinking, folded, and its words, both
              markdown and drawn so; then its tool calls */}
          {turn.thinking && (
            <details className="turn-section turn-thinking">
              <summary>Thinking · {countWords(turn.thinking).toLocaleString("en")} words</summary>
              <MarkdownText text={turn.thinking} isRaw={false} />
            </details>
          )}
          {turn.text && (
            <section className="turn-section turn-text" aria-label="worker output">
              <h4>Worker output</h4>
              <MarkdownText text={turn.text} isRaw={false} />
            </section>
          )}
          {turn.steps.length > 0 && (
            <section className="turn-section" aria-label="tool calls">
              <h4>Tool call{turn.steps.length > 1 ? `s (${turn.steps.length})` : ""}</h4>
              <ol className="tool-calls">
                {turn.steps.map((step, stepIndex) => <ToolCall key={stepIndex} step={step} />)}</ol>
            </section>
          )}
        </li>
      ))}
    </ol>
  );
}
