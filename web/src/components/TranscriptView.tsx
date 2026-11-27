import { useEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode } from "react";
import { fetchJson, formatTime } from "../api";
import { copyText } from "../clipboard";
import Icon from "./Icon";
import Spinner from "./Spinner";
import type { TranscriptLine, TranscriptMatch } from "../transcriptSearch";
import { buildVocabulary, completeWord, findTranscriptMatches } from "../transcriptSearch";

// Phase 1's transcript under the recording, one line per thing said, each line's time a button that plays the
// recording from there. The search box above it lists, while typing, the lines that hold the words typed (near
// spellings too, the closest first), and completes the word being typed from the transcript's own words: Tab takes the
// completion, the arrow keys move through the list, Enter or a click plays the recording from the chosen line. Download
// saves it as text, one line per thing said with its time, and Copy puts that same text on the clipboard. When phase 1 told the speakers apart, the line where a
// speaker's turn starts names the speaker, one colour per speaker; speech two people said at once is "unclear".
// A PDF's text is read, not heard (the owner): with `isPdf` it is "Text", one paragraph a line with no time and no
// speaker, its markup and link addresses taken out
export default function TranscriptView({ runBase, playingS, onPlayFrom, isPdf = false }: {
  runBase: string;
  isPdf?: boolean;
  playingS: number;
  onPlayFrom: (seconds: number) => void;
}) {
  const [lines, setLines] = useState<TranscriptLine[] | null>(null);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [isOpen, setIsOpen] = useState(false);
  const [activeAt, setActiveAt] = useState(-1);
  const [foundAt, setFoundAt] = useState(-1);
  const [isCopied, setIsCopied] = useState(false);
  const rows = useRef<(HTMLLIElement | null)[]>([]);
  const list = useRef<HTMLOListElement>(null);

  useEffect(() => {
    let isCurrent = true;
    fetchJson<{ lines: TranscriptLine[] }>(`${runBase}/transcript`)
      .then((answer) => isCurrent && setLines(isPdf
        ? answer.lines.map((line) => ({ ...line, text: cleanPdfText(line.text), speaker: undefined }))
        : answer.lines))
      .catch((failure: Error) => isCurrent && setError(failure.message));
    return () => {
      isCurrent = false;
    };
  }, [runBase, isPdf]);

  const vocabulary = useMemo(() => buildVocabulary(lines ?? []), [lines]);
  const matches = useMemo(() => findTranscriptMatches(lines ?? [], query), [lines, query]);
  const completion = completeWord(vocabulary, query);
  const playingAt = (lines ?? []).reduce((found, line, at) => (line.start_s <= playingS ? at : found), -1);
  const isListShown = isOpen && query.trim() !== "";

  // The line being played stays in view as the recording plays: the list scrolls, never the page, and only when the
  // line has left its view, a third from its top, so a reader scrolling the list is not pulled back on every line
  useEffect(() => {
    const row = rows.current[playingAt];
    const box = list.current;
    if (!row || !box) return;
    if (row.offsetTop < box.scrollTop || row.offsetTop >= box.scrollTop + box.clientHeight) {
      box.scrollTop = Math.max(0, row.offsetTop - box.clientHeight / 3);
    }
  }, [playingAt]);

  // a PDF's text is no transcript: every line says text
  const noun = isPdf ? "text" : "transcript";
  if (error) return <section className="card transcript"><p className="error">The {noun} could not be read: {error}</p></section>;
  if (lines === null) return <section className="card transcript"><Spinner /></section>;
  if (!lines.length) return <section className="card transcript"><p className="muted">No {noun} yet: phase 1 writes it.</p></section>;

  // The text the download saves, so a copy and a download of one transcript are the same
  const copy = async () => {
    const response = await fetch(`/api${runBase}/transcript/download`);
    await copyText(await response.text());
    setIsCopied(true);
    window.setTimeout(() => setIsCopied(false), 1500);
  };

  const pick = (match: TranscriptMatch | undefined) => {
    if (!match) return;
    onPlayFrom(lines[match.index].start_s);
    setFoundAt(match.index);
    setIsOpen(false);
    setActiveAt(-1);
    rows.current[match.index]?.scrollIntoView({ block: "center", behavior: "smooth" });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Tab" && completion) {
      event.preventDefault();
      setQuery(query + completion);
      setIsOpen(true);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setIsOpen(true);
      setActiveAt((at) => Math.min(at + 1, matches.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveAt((at) => Math.max(at - 1, 0));
    } else if (event.key === "Enter") {
      pick(matches[Math.max(activeAt, 0)]);
    } else if (event.key === "Escape") {
      setIsOpen(false);
    }
  };

  return (
    <section className="card transcript">
      <div className="transcript-head">
        <strong>{isPdf ? "Text" : "Transcript"}</strong>
        <small className="muted">{lines.length} {isPdf ? "paragraphs" : "lines"}</small>
        <a className="btn ghost small" href={`/api${runBase}/transcript/download`} download
          title={isPdf ? "save the text, each paragraph" : "save the transcript as text, each line with its time"}>
          <Icon name="download" size={14} /> Download
        </a>
        <button className="btn ghost small" onClick={copy} title={`copy the ${noun} as the download writes it`}>
          <Icon name={isCopied ? "check" : "copy"} size={14} /> {isCopied ? "Copied" : "Copy"}
        </button>
      </div>
      <div className="transcript-search">
        <div className="transcript-input">
          <input type="search" role="combobox" aria-label={isPdf ? "search the text" : "search the transcript"} aria-expanded={isListShown}
            aria-controls="transcript-suggestions" aria-autocomplete="both" autoComplete="off" spellCheck={false}
            aria-activedescendant={isListShown && activeAt >= 0 ? `transcript-suggestion-${activeAt}` : undefined}
            placeholder={isPdf ? "Search the text" : "Search what was said"} value={query} onKeyDown={onKeyDown}
            onChange={(event) => {
              setQuery(event.target.value);
              setIsOpen(true);
              setActiveAt(-1);
            }}
            onFocus={() => setIsOpen(true)} onBlur={() => setIsOpen(false)} />
          {completion && (
            <div className="transcript-ghost" aria-hidden="true">
              <span className="typed">{query}</span><span data-testid="completion">{completion}</span>
            </div>
          )}
        </div>
        {isListShown && (
          matches.length ? (
            <ul id="transcript-suggestions" role="listbox" className="transcript-suggestions">
              {matches.map((match, at) => (
                <li key={match.index} id={`transcript-suggestion-${at}`} role="option" aria-selected={at === activeAt}
                  className={at === activeAt ? "on" : undefined} onMouseDown={(event) => event.preventDefault()}
                  onMouseEnter={() => setActiveAt(at)} onClick={() => pick(match)}>
                  {!isPdf && <span className="batch-time">{formatTime(lines[match.index].start_s)}</span>}
                  <span>{markRanges(lines[match.index].text, match.ranges)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="transcript-suggestions muted">No line holds “{query.trim()}”.</p>
          )
        )}
      </div>
      <ol className="transcript-lines" ref={list}>
        {lines.map((line, at) => (
          <li key={`${line.start_s}-${at}`} ref={(row) => { rows.current[at] = row; }}
            className={[at === playingAt ? "on" : "", at === foundAt ? "found" : ""].join(" ").trim() || undefined}
            aria-current={at === playingAt || undefined}>
            {!isPdf && (
              <button className="batch-time" title={`play from ${formatTime(line.start_s)}`}
                onClick={() => onPlayFrom(line.start_s)}>
                {formatTime(line.start_s)}
              </button>
            )}
            <span>
              {speakerAt(lines, at) && (
                <span className={`speaker-tag speaker-${speakerHue(speakerAt(lines, at))}`}>{speakerAt(lines, at)}</span>
              )}
              {line.text}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

// The speaker to name before line `at`: its own, where it differs from the line before; "" inside a turn or with no
// speakers at all. A line with no speaker in a transcript that has them is "unclear".
function speakerAt(lines: TranscriptLine[], at: number): string {
  if (lines[at].speaker === undefined) return "";
  const speaker = lines[at].speaker ?? "unclear";
  const before = at > 0 ? lines[at - 1].speaker ?? "unclear" : "";
  return speaker === before ? "" : speaker;
}

// One of six colours per speaker, from the speaker's letter: Speaker A and Speaker G share one.
function speakerHue(speaker: string): number {
  return speaker === "unclear" ? 0 : (speaker.charCodeAt(speaker.length - 1) - 65) % 6 + 1;
}

// The text with each matched word in a `<mark>`, the ranges sorted and apart.
function markRanges(text: string, ranges: [number, number][]): ReactNode[] {
  const parts: ReactNode[] = [];
  let from = 0;
  ranges.forEach(([start, end], at) => {
    if (start < from) return;
    parts.push(text.slice(from, start), <mark key={at}>{text.slice(start, end)}</mark>);
    from = end;
  });
  parts.push(text.slice(from));
  return parts;
}

// A PDF paragraph as a reader reads it: phase 1's markdown marks, underline tags and link addresses taken out
export function cleanPdfText(text: string): string {
  return text
    .replace(/\s*<https?:\/\/[^>\s]*>/g, "")
    .replace(/<\/?u>/g, "")
    .replace(/~~/g, "")
    .replace(/\*\*|__/g, "")
    .replace(/(^|\s)_(\S[^_]*?)_(?=\s|[.,;:)]|$)/g, "$1$2")
    .replace(/^#+\s*/, "")
    .replace(/\s+([.,;:)])/g, "$1")
    .replace(/\s{2,}/g, " ")
    .trim();
}
