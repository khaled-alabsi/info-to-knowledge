// Small stroke icons, drawn inline so the page loads nothing from outside.
const PATHS: Record<string, string> = {
  play: "M7 4.5v15l12-7.5z",
  pause: "M8 5v14M16 5v14",
  stop: "M6 6h12v12H6z",
  more: "M5 12h.01M12 12h.01M19 12h.01",
  chevron: "M6 9l6 6 6-6",
  upload: "M12 16V4M7 9l5-5 5 5M4 20h16",
  back: "M15 18l-6-6 6-6",
  copy: "M9 9h11v11H9zM5 15H4V4h11v1",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  check: "M5 13l4 4L19 7",
  settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z",
  x: "M6 6l12 12M18 6L6 18",
  search: "M11 18a7 7 0 1 1 0-14 7 7 0 0 1 0 14zM21 21l-5-5",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  inbox: "M4 13l2.5-8h11L20 13v6H4zM4 13h5l1 2h4l1-2h5",
  list: "M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01",
  filter: "M3 5h18l-7 8v6l-4 2v-8z",
  layers: "M12 3l9 5-9 5-9-5zM3 13l9 5 9-5",
  // An arrow turning back: at 16 px a circular arrow read as a plain circle
  reset: "M9 14L4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11",
  terminal: "M5 7l5 5-5 5M12 17h7",
  help: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.5 9.2a2.6 2.6 0 0 1 5 .9c0 1.7-2.5 2.3-2.5 3.6M12 17h.01",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  restore: "M4 12a8 8 0 1 0 2.3-5.7M4 4v5h5",
  archive: "M3 4h18v4H3zM5 8v12h14V8M10 12h4",
  pencil: "M4 20h4L19 9l-4-4L4 16v4zM14 6l4 4",
  download: "M12 4v12M7 11l5 5 5-5M4 20h16",
  expand: "M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5",
  lock: "M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4",
  unlock: "M5 11h14v10H5zM8 11V7a4 4 0 0 1 7.5-2",
  // A speedometer: the dial's arc and its needle. How fast something is going, never how long it has run.
  // A warning triangle with its mark: something a run needs is not set up
  alert: "M12 3l10 18H2zM12 10v5M12 18h.01",
  plus: "M12 5v14M5 12h14",
  // bars of a sound: an audio recording
  wave: "M3 12h2M7 9v6M11 5v14M15 8v8M19 11v2",
  // two lines joining into one: recordings merged into one recording in parts
  merge: "M6 4v4a6 6 0 0 0 6 6v6M18 4v4a6 6 0 0 1-6 6M9 17l3 3 3-3",
  // two stops on a line with the span between them: the part of the input a run processes
  range: "M4 5v14M20 5v14M8 12h8M10 9l-3 3 3 3M14 9l3 3-3 3",
  arrowUp: "M12 19V5M6 11l6-6 6 6",
  gauge: "M4 18a8 8 0 1 1 16 0M12 18l4.5-5.5",
};

export default function Icon({ name, size = 16 }: { name: string; size?: number }) {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  );
}
