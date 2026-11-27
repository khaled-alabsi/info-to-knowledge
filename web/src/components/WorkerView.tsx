import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useMemo, useRef, useState } from "react";
import { buildFileUrl, describeError, fetchJson, formatPhaseTitle } from "../api";
import type { InstructionPreview, Phase, WorkerAttempt, WorkerInputSegment, WorkerStep, WorkerTurn } from "../api";
import CopyButton from "./CopyButton";
import Dialog from "./Dialog";
import Icon from "./Icon";
import Spinner from "./Spinner";

// "What the worker saw": one phase's steps, each attempt's instructions and input as the run saved
// them, a conversation's turns, and a preview of the instructions the current files would send. Read only from disk.

type Tab = "instructions" | "input" | "turns";

// Instructions and requests are markdown: drawn as a reader reads them, or with `isRaw` as the exact characters sent
export function MarkdownText({ text, isRaw }: { text: string; isRaw: boolean }) {
  const html = useMemo(() => DOMPurify.sanitize(marked.parse(text, { async: false }) as string), [text]);
  if (isRaw) return <pre className="worker-text">{text}</pre>;
  return <div className="markdown worker-markdown" dangerouslySetInnerHTML={{ __html: html }} />;
}

function InputView({ runBase, segments, isRaw }: { runBase: string; segments: WorkerInputSegment[]; isRaw: boolean }) {
  const [enlarged, setEnlarged] = useState<string | null>(null);
  if (segments.length === 0) return <p className="muted">The run saved no input for this step.</p>;
  return (
    <div className="worker-input">
      {segments.map((segment, index) => segment.kind === "text" ? (
        <MarkdownText key={index} text={segment.text} isRaw={isRaw} />
      ) : segment.path ? (
        <figure key={index} className={`worker-frame${enlarged === segment.path ? " enlarged" : ""}`}>
          <button type="button" className="worker-frame-button"
                  aria-label={enlarged === segment.path ? `make frame ${segment.frame} small again` : `enlarge frame ${segment.frame}`}
                  onClick={() => setEnlarged(enlarged === segment.path ? null : segment.path)}>
            <img src={buildFileUrl(runBase, segment.path)} alt={`frame ${segment.frame}`} loading="lazy" />
          </button>
          <figcaption>image: frame {segment.frame}</figcaption>
        </figure>
      ) : (
        <p key={index} className="worker-frame missing">an image whose frame the text before it does not name</p>
      ))}
    </div>
  );
}

function TurnsView({ turns }: { turns: WorkerTurn[] }) {
  if (turns.length === 0) return <p className="muted">No turn of this follow-up is in the conversation's log.</p>;
  return (
    <ol className="worker-turns">
      {turns.map((turn, index) => (
        <li key={index} className={`worker-turn ${turn.kind}`}>
          {turn.kind === "turn" ? (
            <>
              <strong>worker{turn.prompt_tokens ? ` · ${turn.prompt_tokens.toLocaleString("en")} tokens in` : ""}</strong>
              {turn.thinking && <details><summary>thinking</summary><pre className="worker-text">{turn.thinking}</pre></details>}
              {turn.said && <pre className="worker-text">{turn.said}</pre>}
              {(turn.calls ?? []).map((call, callIndex) => (
                <pre key={callIndex} className="worker-text call">{call.name}({JSON.stringify(call.args, null, 1)})</pre>
              ))}
            </>
          ) : turn.kind === "result" ? (
            <>
              <strong>result of {turn.tool}</strong>
              <pre className="worker-text">{turn.result}</pre>
            </>
          ) : (
            // an accepted turn's text names its kind already ("accepted after 1 rejection"), so the kind is not written twice
            turn.kind === "accepted" ? <strong>{nameStatus(turn.text ?? "")}</strong>
              : <><strong>{turn.kind}</strong> <span>{nameStatus(turn.text ?? "")}</span></>
          )}
        </li>
      ))}
    </ol>
  );
}

function PreviewView({ phase, isAudio, isRaw }: { phase: Phase; isAudio: boolean; isRaw: boolean }) {
  const [previews, setPreviews] = useState<InstructionPreview[] | null>(null);
  const [chosen, setChosen] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    let isCurrent = true;
    fetchJson<{ instructions: InstructionPreview[] }>(`/phase-instructions?phase=${phase.number}&audio=${isAudio}`)
      .then((answer) => {
        if (!isCurrent) return;
        setPreviews(answer.instructions);
        setChosen(answer.instructions[0]?.name ?? "");
      })
      .catch((problem) => { if (isCurrent) setError(describeError(problem)); });
    return () => { isCurrent = false; };
  }, [phase.number, isAudio]);

  if (error) return <p className="error">{error}</p>;
  if (previews === null) return <Spinner />;
  if (previews.length === 0) return <p className="muted">This phase sends no model instructions.</p>;
  const shown = previews.find((preview) => preview.name === chosen) ?? previews[0];
  return (
    <div className="worker-preview">
      <p className="muted">Built now from the current instruction files, as a run builds them for a step with no fields
        of its own, for this run's {isAudio ? "audio" : "video"} recording; a run adds its recording's profile and tags.
        No model is called.</p>
      <div className="worker-tabs" role="tablist" aria-label="instruction sets">
        {previews.map((preview) => (
          <button key={preview.name} role="tab" aria-selected={preview.name === shown.name}
                  className={preview.name === shown.name ? "on" : ""} onClick={() => setChosen(preview.name)}>
            {preview.name}
          </button>
        ))}
      </div>
      {shown.used_by && <p className="worker-used-by"><strong>Who gets it:</strong> {shown.used_by}</p>}
      {shown.error ? <p className="error">{shown.error}</p> : <MarkdownText text={shown.text} isRaw={isRaw} />}
    </div>
  );
}

// A step named as the batch table names it: the frames it covers and the step ("frames-000-007-answers" as
// "0–7 · answers"; a batch's own slug is its reading step), never the file's slug
function nameStep(step: string): string {
  const match = step.match(/^frames-0*(\d+)-0*(\d+)-?(.*)$/);
  if (!match) return step.replace(/-/g, " ");
  return `${match[1]}–${match[2]} · ${match[3] ? match[3].replace(/-/g, " ") : "reading"}`;
}

// The server's state words in the page's vocabulary: an "answer" is a phase 3 step's name, a try is an attempt
function nameStatus(status: string): string {
  return status.replace(/refused answer(\(s\)|s)?/g, (_, plural) => `rejection${plural ?? ""}`).replace(/\banswer\b/g, "attempt");
}

// A step's last state, and how many attempts were rejected before an accepted one ("accepted after 2 rejected"), so a
// step that passed in the end is still found among the rejected ones
function describeStepStatus(step: WorkerStep): string {
  const last = step.attempts[step.attempts.length - 1]?.status ?? "";
  const rejected = step.attempts.slice(0, -1).filter((attempt) => attempt.status !== "accepted").length;

  return last === "accepted" && rejected ? `accepted after ${rejected} rejected` : nameStatus(last);
}

function hasRejection(step: WorkerStep): boolean {
  return step.attempts.some((attempt) => attempt.status !== "accepted");
}

// A step's newest attempt, the one the view opens it on; a step with no attempt has none
function readLastAttemptRef(step: WorkerStep | undefined): string {
  return step?.attempts.at(-1)?.ref ?? "";
}

// `initialStep` opens the view on that step (a Blocks row or a table's rejected row names it), else on the first step
// not accepted
export default function WorkerView({ runBase, phase, isAudio, onClose, initialStep = "" }:
  { runBase: string; phase: Phase; isAudio: boolean; onClose: () => void; initialStep?: string }) {
  const [steps, setSteps] = useState<WorkerStep[] | null>(null);
  const [stepAt, setStepAt] = useState(0);
  // the server's reference of the attempt open
  const [attemptRef, setAttemptRef] = useState("");
  const [attempt, setAttempt] = useState<WorkerAttempt | null>(null);
  const [tab, setTab] = useState<Tab>("instructions");
  const [isPreview, setIsPreview] = useState(false);
  const [isRaw, setIsRaw] = useState(false);
  const [isOnlyRejected, setIsOnlyRejected] = useState(false);
  const detailBox = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let isCurrent = true;
    fetchJson<{ steps: WorkerStep[] }>(`${runBase}/worker?phase=${phase.number}`)
      .then((answer) => {
        if (!isCurrent) return;
        setSteps(answer.steps);
        // it opens on the named step, else on the first step not accepted (the one a reader comes to see), else the first
        const named = initialStep ? answer.steps.findIndex((step) => step.step === initialStep) : -1;
        const at = named >= 0 ? named
          : Math.max(0, answer.steps.findIndex((step) => step.attempts[step.attempts.length - 1]?.status !== "accepted"));
        setStepAt(at);
        setAttemptRef(readLastAttemptRef(answer.steps[at]));
      })
      .catch((problem) => { if (isCurrent) setError(describeError(problem)); });
    return () => { isCurrent = false; };
  }, [runBase, phase.number, initialStep]);

  useEffect(() => {
    if (!attemptRef) return;
    let isCurrent = true;
    setAttempt(null);
    // an error of the attempt open before is not this one's
    setError("");
    fetchJson<WorkerAttempt>(`${runBase}/worker/attempt?ref=${encodeURIComponent(attemptRef)}`)
      .then((answer) => {
        if (!isCurrent) return;
        setAttempt(answer);
        setTab((current) => current === "turns" && answer.kind !== "conversation" ? "instructions" : current);
      })
      .catch((problem) => { if (isCurrent) setError(describeError(problem)); });
    return () => { isCurrent = false; };
  }, [runBase, attemptRef]);

  const chooseStep = (index: number) => {
    setStepAt(index);
    setAttemptRef(readLastAttemptRef(steps?.[index]));
    setIsPreview(false);
  };
  const step = steps?.[stepAt];

  return (
    <Dialog title={`What the worker saw · ${formatPhaseTitle(phase)}`} onClose={onClose}>
      <div className="worker-view">
        <div className="worker-actions">
          {/* what the open step's tab shows (instructions, input or turns) and why it was rejected, as text */}
          <CopyButton className="btn small" getText={() => detailBox.current?.textContent?.trim() ?? ""} />
          <button type="button" className={`btn small${isRaw ? " active" : ""}`} aria-pressed={isRaw}
                  title="show the exact characters sent instead of the drawn markdown" onClick={() => setIsRaw(!isRaw)}>
            Raw text
          </button>
          <button type="button" className={`btn small${isPreview ? " active" : ""}`} aria-pressed={isPreview}
                  onClick={() => setIsPreview(!isPreview)}>
            <Icon name="eye" size={14} /> Preview instructions
          </button>
        </div>
        {error && <p className="error">{error}</p>}
        {isPreview ? <PreviewView phase={phase} isAudio={isAudio} isRaw={isRaw} /> : steps === null ? <Spinner />
          : steps.length === 0 ? (
            <p className="muted">Nothing to show yet: this phase has not called its model in this run. A phase that ran before
              this viewer existed kept no calls.</p>
          ) : (
            <div className="worker-columns">
              <div className="worker-step-list">
              <label className="switch small-switch">
                <input type="checkbox" checked={isOnlyRejected} onChange={(event) => setIsOnlyRejected(event.target.checked)} />
                <span className="track" />
                <span>Only steps with a rejection</span>
              </label>
              <ul className="worker-steps" aria-label="steps">
                {steps.map((item, index) => (!isOnlyRejected || hasRejection(item)) && (
                  <li key={`${item.step}-${item.is_dry_run}`}>
                    <button type="button" className={`worker-step${index === stepAt ? " active" : ""}`}
                            aria-current={index === stepAt} onClick={() => chooseStep(index)}>
                      <span>{nameStep(item.step)}</span>
                      <small className={`chip ${item.attempts[item.attempts.length - 1]?.status.replace(/ /g, "-")}`}>
                        {describeStepStatus(item)}
                      </small>
                    </button>
                  </li>
                ))}
              </ul>
              </div>
              <div className="worker-detail" ref={detailBox}>
                {step && step.attempts.length > 1 && (
                  <div className="worker-attempts" role="group" aria-label="attempts">
                    {step.attempts.map((item) => (
                      <button key={item.ref} type="button" className={`btn small${item.ref === attemptRef ? " active" : ""}`}
                              aria-pressed={item.ref === attemptRef} title={item.session} onClick={() => setAttemptRef(item.ref)}>
                        {item.label} · {nameStatus(item.status)}
                      </button>
                    ))}
                  </div>
                )}
                <div className="worker-tabs" role="tablist" aria-label="what the worker saw">
                  {(["instructions", "input", ...(attempt?.kind === "conversation" ? ["turns"] : [])] as Tab[]).map((name) => (
                    <button key={name} role="tab" aria-selected={tab === name} className={tab === name ? "on" : ""}
                            onClick={() => setTab(name)}>
                      {name === "instructions" ? "Instructions" : name === "input" ? "Input" : "Turns"}
                    </button>
                  ))}
                </div>
                {attempt === null ? <Spinner /> : (
                  <>
                    {attempt.findings.length > 0 && (
                      <div className="worker-findings" aria-label="why this attempt was rejected">
                        <strong>Rejected:</strong>
                        <ul>{attempt.findings.map((finding, index) => (
                          <li key={index}><code>{finding.code}</code> {finding.detail}</li>
                        ))}</ul>
                      </div>
                    )}
                    {tab === "instructions" && (attempt.instructions !== null
                      ? <MarkdownText text={attempt.instructions} isRaw={isRaw} />
                      : <p className="muted">This run did not save the instructions of this step; runs from now on do.</p>)}
                    {tab === "input" && <InputView runBase={runBase} segments={attempt.input} isRaw={isRaw} />}
                    {tab === "turns" && <TurnsView turns={attempt.turns} />}
                  </>
                )}
              </div>
            </div>
          )}
      </div>
    </Dialog>
  );
}
