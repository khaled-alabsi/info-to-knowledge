// A run's or a job's updates, asked for once a second with a short request: an open event stream per detail view took
// every connection Firefox allows one server (6), and a recording's video then waited for good. `useUpdateStream`
// reads them, and a test swaps the source.

// One answer: the state, the log text added since the offset asked from, and the offset to ask from next
export interface StreamUpdate {
  offset: number;
  text?: string;
}

export interface UpdateSource {
  onupdate: ((update: StreamUpdate) => void) | null;
  // a failed ask: the server's status when it answered and refused, null when nothing answered
  onerror: ((status: number | null) => void) | null;
  close(): void;
}

// How long between two asks, and how long after a failed ask the source is opened again
export const UPDATE_INTERVAL_MS = 1000;
export const RETRY_AFTER_ERROR_MS = 2000;

export class PolledUpdateSource implements UpdateSource {
  onupdate: ((update: StreamUpdate) => void) | null = null;
  onerror: ((status: number | null) => void) | null = null;
  private timer: number | undefined;
  private isClosed = false;

  // `buildUrl` gives the address of each ask: its reader owns the offset and puts it there
  constructor(private readonly buildUrl: () => string) {
    this.timer = window.setTimeout(() => void this.ask(), 0);
  }

  close() {
    this.isClosed = true;
    window.clearTimeout(this.timer);
  }

  // One answer handed on, then the next ask a second later
  private async ask() {
    // a failed ask ends the source: its reader opens a new one, from where it stopped, or none when the server says
    // there is nothing to ask about
    let update: StreamUpdate;
    try {
      const answer = await fetch(this.buildUrl());
      if (!answer.ok) {
        if (!this.isClosed) this.onerror?.(answer.status);
        return;
      }
      update = await answer.json();
    } catch {
      if (!this.isClosed) this.onerror?.(null);
      return;
    }
    if (this.isClosed) return;
    this.onupdate?.(update);
    this.timer = window.setTimeout(() => void this.ask(), UPDATE_INTERVAL_MS);
  }
}

let openSource: (buildUrl: () => string) => UpdateSource = (buildUrl) => new PolledUpdateSource(buildUrl);

export function openUpdateSource(buildUrl: () => string): UpdateSource {
  return openSource(buildUrl);
}

// A test's stand-in; returns the one it replaced
export function setUpdateSourceOpener(opener: (buildUrl: () => string) => UpdateSource): (buildUrl: () => string) => UpdateSource {
  const previous = openSource;
  openSource = opener;
  return previous;
}
