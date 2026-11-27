import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

export interface PlayerPart {
  name: string;
  size_bytes: number;
  start_s?: number;
}

// The part a second of the meeting falls in, and the second inside that part: the last part starting at or before it.
export function findPartAt(parts: PlayerPart[], seconds: number): { index: number; offset: number } {
  let index = 0;
  parts.forEach((part, at) => {
    if ((part.start_s ?? 0) <= seconds) index = at;
  });
  return { index, offset: Math.max(0, seconds - (parts[index]?.start_s ?? 0)) };
}

// An audio part has no picture: it plays in an <audio> element, which shows no empty black box
const AUDIO_NAME = /\.(mp3|m4a|wav|aac|flac|ogg|opus)$/i;
// A PDF has no time to play: the browser's own viewer shows its pages
const PDF_NAME = /\.pdf$/i;

// One recording that may come in several files: a button per part, the next part playing when one ends, and a second
// of the whole meeting (`seekTo`) opened in the part it falls in. `children` renders tools for the part on screen, and
// `onTimeUpdate` hears the second of the whole meeting playing.
export default function PartsPlayer({ parts, buildSrc, seekTo = null, onSeeked, onTimeUpdate, isAutoPlaying = false,
  isSeekPlaying = true, className, children }: {
  parts: PlayerPart[];
  buildSrc: (index: number) => string;
  seekTo?: number | null;
  onSeeked?: () => void;
  onTimeUpdate?: (seconds: number) => void;
  isAutoPlaying?: boolean;
  // whether a second asked for (`seekTo`) plays from there; the Range's handles only show the frame they stand on
  isSeekPlaying?: boolean;
  className?: string;
  children?: (index: number) => ReactNode;
}) {
  const [index, setIndex] = useState(0);
  const pendingOffset = useRef<number | null>(null);
  const shouldPlay = useRef(isAutoPlaying);
  const player = useRef<HTMLMediaElement | null>(null);

  const startAt = (video: HTMLMediaElement) => {
    if (pendingOffset.current !== null) {
      video.currentTime = pendingOffset.current;
      pendingOffset.current = null;
    }
    // a browser may refuse to play before the person clicks: the player's own controls stay for them
    if (shouldPlay.current) video.play().catch(() => undefined);
  };

  useEffect(() => {
    if (seekTo === null) return;
    const target = findPartAt(parts, seekTo);
    pendingOffset.current = target.offset;
    shouldPlay.current = isSeekPlaying;
    onSeeked?.();
    if (target.index !== index) {
      setIndex(target.index);
      return;
    }
    const video = player.current;
    if (video && video.readyState >= 1) startAt(video);
    // only a new second asked for moves the player: the part on screen and the parts list changing must not seek again
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seekTo]);

  const choosePart = (next: number) => {
    shouldPlay.current = true;
    pendingOffset.current = 0;
    setIndex(next);
  };

  const name = parts[index]?.name ?? "";
  const isPdf = PDF_NAME.test(name);
  const MediaTag = AUDIO_NAME.test(name) ? "audio" : "video";

  return (
    <div className={className}>
      {parts.length > 1 && (
        <div className="part-tabs" role="tablist" aria-label="parts">
          {parts.map((part, at) => (
            <button key={part.name} role="tab" aria-selected={at === index} className={`chip${at === index ? " on" : ""}`}
              title={part.name} onClick={() => choosePart(at)}>
              Part {at + 1}
            </button>
          ))}
        </div>
      )}
      {children?.(index)}
      {isPdf
        ? <iframe key={index} className="recording-pdf" title={parts[index].name} src={buildSrc(index)} />
        : <MediaTag key={index} ref={(element: HTMLMediaElement | null) => { player.current = element; }} controls preload="metadata"
            src={buildSrc(index)} onLoadedMetadata={(event) => startAt(event.currentTarget)}
            onTimeUpdate={(event) => onTimeUpdate?.((parts[index]?.start_s ?? 0) + event.currentTarget.currentTime)}
            onEnded={() => { if (index < parts.length - 1) choosePart(index + 1); }} />}
    </div>
  );
}
