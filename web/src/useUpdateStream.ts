import { useEffect, useState } from "react";
import { RETRY_AFTER_ERROR_MS, openUpdateSource } from "./updateSource";
import type { StreamUpdate, UpdateSource } from "./updateSource";

// One update a second from `path` (`updateSource.ts`): the newest state, and the log text added since it was opened.
// A new `path` starts over with an empty log. The offset to ask from is kept here alone, and each ask carries it.
export function useUpdateStream<T extends StreamUpdate>(path: string | null): { latest: T | null; text: string } {
  const [latest, setLatest] = useState<T | null>(null);
  const [text, setText] = useState("");

  useEffect(() => {
    setLatest(null);
    setText("");
    if (!path) return;
    let offset = 0;
    let source: UpdateSource | null = null;
    let retry: number | undefined;
    const buildUrl = () => `/api${path}${path.includes("?") ? "&" : "?"}offset=${offset}`;
    const open = () => {
      source = openUpdateSource(buildUrl);
      // A failed ask closes the source, and it is opened again from where it stopped; a run or job the server no
      // longer knows (404) is asked no more, and the page that showed it decides what it shows now
      source.onerror = (status) => {
        source?.close();
        if (status !== 404) retry = window.setTimeout(open, RETRY_AFTER_ERROR_MS);
      };
      source.onupdate = (update) => {
        // a shorter offset means the log was replaced by a newer one: start the text again
        if (update.offset < offset) setText("");
        offset = update.offset;
        setLatest(update as T);
        if (update.text) setText((previous) => trimLog(previous + update.text));
      };
    };
    open();
    return () => {
      window.clearTimeout(retry);
      source?.close();
    };
  }, [path]);

  return { latest, text };
}

// The page keeps the last 400,000 characters of a log; the whole file stays in the run folder.
export const LOG_KEEP_CHARS = 400_000;

function trimLog(text: string): string {
  return text.length > LOG_KEEP_CHARS ? text.slice(text.length - LOG_KEEP_CHARS) : text;
}
