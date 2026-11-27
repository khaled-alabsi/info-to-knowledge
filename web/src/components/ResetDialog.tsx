import { useEffect, useRef, useState } from "react";
import { describeError, formatPhaseTitle, readResetOutput, sendJson } from "../api";
import type { CommandOutput, Phase } from "../api";
import Dialog from "./Dialog";

export type ResetKind = "phase" | "chapter";

// The run page's Reset to phase and Reset from chapter: what the reset would remove is listed as the dialog opens and
// each time a choice changes, and Reset runs it. Only the answer to the last choice is shown, never a slower one asked
// before it; a listing that fails says why in its place.
export default function ResetDialog({ runBase, runName, kind, initialNumber, phases, resetMax, chapters, onClose, onReset, onMessage }: {
  runBase: string;
  runName: string;
  kind: ResetKind;
  initialNumber: number;
  // the run's phases a phase reset can keep up to, and the highest it can keep
  phases: Phase[];
  resetMax: number;
  // the run's chapters by number and title, once its agenda is written
  chapters: { number: number; title: string }[];
  onClose: () => void;
  // after a reset that ran: the lists change
  onReset: () => void;
  onMessage: (text: string, isError?: boolean) => void;
}) {
  const [resetNumber, setResetNumber] = useState(initialNumber);
  // a chapter reset that keeps the chapters' headings and writes only their texts again
  const [isTextOnly, setIsTextOnly] = useState(false);
  const [listing, setListing] = useState<CommandOutput | null>(null);
  const [isResetting, setIsResetting] = useState(false);
  const listingRequest = useRef(0);

  const buildRequest = (isConfirmed: boolean) => ({
    kind, number: resetNumber, is_confirmed: isConfirmed, ...(kind === "chapter" ? { is_text_only: isTextOnly } : {}),
  });

  // Each listing takes seconds: a newer choice makes an older answer, or an older failure, of no use
  useEffect(() => {
    const request = ++listingRequest.current;
    setListing(null);
    sendJson<CommandOutput>(`${runBase}/reset`, buildRequest(false))
      .then((answer) => { if (request === listingRequest.current) setListing(answer); })
      .catch((error) => {
        if (request === listingRequest.current) setListing({ exit_code: 1, output: describeError(error) });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, resetNumber, isTextOnly]);

  // One line for the page's message: what was reset and how many things went, never the whole list
  const describeReset = (answer: CommandOutput) => {
    const { refusal, removedCount } = readResetOutput(answer);
    if (refusal) return refusal;
    const what = kind === "phase" ? `reset to phase ${resetNumber}` : `reset from chapter ${resetNumber}`;
    return removedCount ? `${runName}: ${what}, ${removedCount} removed` : `${runName}: ${what}, nothing to remove`;
  };

  const reset = async () => {
    setIsResetting(true);
    try {
      const answer = await sendJson<CommandOutput>(`${runBase}/reset`, buildRequest(true));
      onMessage(describeReset(answer), answer.exit_code !== 0);
      onClose();
      onReset();
    } catch (error) {
      onMessage(`${runName}: the reset was not run: ${describeError(error)}`, true);
    } finally {
      setIsResetting(false);
    }
  };

  return (
    <Dialog title={kind === "phase" ? "Reset to phase" : "Reset from chapter"} onClose={onClose}>
      <p className="muted">
        {kind === "phase"
          ? "Removes every file and progress record of the phases after the one chosen below, as if they never ran. 0 removes every phase."
          : "Removes the chosen chapter and every later chapter of phase 4, so the next resume writes them again."}
      </p>
      <div className="dialog-row">
        <label className="field">
          <span>{kind === "phase" ? "Keep phases up to" : "Chapter"}</span>
          {kind === "phase" && (
            <select value={resetNumber} onChange={(event) => setResetNumber(Number(event.target.value))}>
              <option value={0}>0 · none, every phase removed</option>
              {phases.filter((phase) => phase.number <= resetMax).map((phase) => (
                <option key={phase.number} value={phase.number}>{phase.number} · {formatPhaseTitle(phase)}</option>
              ))}
            </select>
          )}
          {kind === "chapter" && chapters.length > 0 && (
            <select value={resetNumber} onChange={(event) => setResetNumber(Number(event.target.value))}>
              {chapters.map((chapter) => (
                <option key={chapter.number} value={chapter.number}>{chapter.number} · {chapter.title}</option>
              ))}
            </select>
          )}
          {kind === "chapter" && chapters.length === 0 && (
            <input type="number" min={1} value={resetNumber} onChange={(event) => setResetNumber(Math.max(1, Number(event.target.value)))} />
          )}
        </label>
      </div>
      {kind === "chapter" && (
        <label className="switch">
          <input type="checkbox" checked={isTextOnly} onChange={(event) => setIsTextOnly(event.target.checked)} />
          <span className="track" />
          <span>Keep the headings <small>write only the chapters' texts again</small></span>
        </label>
      )}
      {listing ? <pre className="listing">{listing.output}</pre>
        : <p className="muted"><span className="spinner" aria-hidden="true" /> Listing what this reset removes…</p>}
      <div className="dialog-actions">
        <button className="btn ghost" onClick={onClose}>Cancel</button>
        <button className="btn danger-solid" disabled={isResetting} onClick={() => void reset()}>
          {isResetting ? <><span className="spinner" aria-hidden="true" /> Resetting…</> : "Reset"}
        </button>
      </div>
    </Dialog>
  );
}
