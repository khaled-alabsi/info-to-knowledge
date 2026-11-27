import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { PointerEvent } from "react";
import Icon from "./Icon";
import * as focusTrap from "../focusTrap";

// What the viewer shows: a drawn diagram (its SVG markup and the Mermaid source it came from) or an image.
export type FigureContent =
  | { kind: "svg"; markup: string; width: number; height: number; source: string; name: string }
  | { kind: "img"; src: string; name: string };

interface Viewport {
  x: number;
  y: number;
  k: number;
}

const MIN_SCALE = 0.05;
const MAX_SCALE = 12;
const FIT_MARGIN_PX = 60;

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

// Full screen, over everything: drag to pan, wheel or pinch to zoom about the pointer, Fit (or 0) to see it whole,
// Esc to leave. A diagram can be saved as SVG, as PNG at twice its size, or as the Mermaid source it was drawn from.
export default function FigureViewer({ content, caption, onClose }: {
  content: FigureContent;
  caption: string;
  onClose: () => void;
}) {
  const stage = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<Viewport>({ x: 0, y: 0, k: 1 });
  const closeButton = useRef<HTMLButtonElement>(null);
  const overlay = useRef<HTMLDivElement>(null);
  const [natural, setNatural] = useState(content.kind === "svg"
    ? { w: content.width, h: content.height } : { w: 0, h: 0 });
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef(0);

  const fit = useCallback(() => {
    const box = stage.current?.getBoundingClientRect();
    if (!box || !natural.w || !natural.h) return;
    const k = Math.min((box.width - FIT_MARGIN_PX) / natural.w, (box.height - FIT_MARGIN_PX) / natural.h, 4);
    setView({ k, x: (box.width - natural.w * k) / 2, y: (box.height - natural.h * k) / 2 });
  }, [natural]);

  useLayoutEffect(fit, [fit]);

  const zoomAbout = useCallback((factor: number, cx: number, cy: number) => {
    setView((current) => {
      const k = Math.min(MAX_SCALE, Math.max(MIN_SCALE, current.k * factor));
      const ratio = k / current.k;
      return { k, x: cx - (cx - current.x) * ratio, y: cy - (cy - current.y) * ratio };
    });
  }, []);

  const zoomCentre = (factor: number) => {
    const box = stage.current?.getBoundingClientRect();
    if (box) zoomAbout(factor, box.width / 2, box.height / 2);
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key === "0") fit();
      if (event.key === "+" || event.key === "=") zoomCentre(1.25);
      if (event.key === "-") zoomCentre(0.8);
      // the arrow keys move the view, as a drag does
      const step = { ArrowLeft: [60, 0], ArrowRight: [-60, 0], ArrowUp: [0, 60], ArrowDown: [0, -60] }[event.key];
      if (step) {
        event.preventDefault();
        setView((current) => ({ ...current, x: current.x + step[0], y: current.y + step[1] }));
      }
      focusTrap.keepTabInside(event, overlay.current);
    };
    document.body.classList.add("no-scroll");
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.classList.remove("no-scroll");
      window.removeEventListener("keydown", onKey);
    };
  });

  // A trackpad pinch arrives as a wheel event with ctrlKey; a mouse wheel without. Both zoom here, and the listener is
  // not passive, so the pinch never zooms the browser page instead.
  useEffect(() => {
    const element = stage.current;
    if (!element) return;
    const onWheel = (event: globalThis.WheelEvent) => {
      event.preventDefault();
      const box = element.getBoundingClientRect();
      zoomAbout(Math.exp(-event.deltaY * (event.ctrlKey ? 0.01 : 0.0025)), event.clientX - box.left, event.clientY - box.top);
    };
    element.addEventListener("wheel", onWheel, { passive: false });
    return () => element.removeEventListener("wheel", onWheel);
  }, [zoomAbout]);

  const onPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    stage.current?.setPointerCapture(event.pointerId);
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
  };

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const previous = pointers.current.get(event.pointerId);
    if (!previous) return;
    pointers.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    const points = [...pointers.current.values()];
    if (points.length >= 2) {
      const spread = Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y);
      const box = stage.current?.getBoundingClientRect();
      if (pinch.current && box) {
        zoomAbout(spread / pinch.current, (points[0].x + points[1].x) / 2 - box.left,
          (points[0].y + points[1].y) / 2 - box.top);
      }
      pinch.current = spread;
      return;
    }
    setView((current) => ({ ...current, x: current.x + event.clientX - previous.x, y: current.y + event.clientY - previous.y }));
  };

  const onPointerUp = (event: PointerEvent<HTMLDivElement>) => {
    pointers.current.delete(event.pointerId);
    if (pointers.current.size < 2) pinch.current = 0;
  };

  const exportAs = (kind: "svg" | "png" | "mmd") => {
    if (content.kind !== "svg") return;
    if (kind === "mmd") {
      saveBlob(new Blob([content.source + "\n"], { type: "text/plain" }), `${content.name}.mmd`);
      return;
    }
    if (kind === "svg") {
      saveBlob(new Blob([content.markup], { type: "image/svg+xml" }), `${content.name}.svg`);
      return;
    }
    const scale = 2;
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(content.width * scale));
    canvas.height = Math.max(1, Math.round(content.height * scale));
    const image = new Image();
    image.onload = () => {
      const context = canvas.getContext("2d");
      if (!context) return;
      context.fillStyle = getComputedStyle(document.body).backgroundColor || "#fff";
      context.fillRect(0, 0, canvas.width, canvas.height);
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => blob && saveBlob(blob, `${content.name}.png`));
    };
    image.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(content.markup);
  };

  // The viewer covers the page: the keyboard starts on its Close button and goes back where it was when it closes
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    closeButton.current?.focus();
    return () => before?.focus();
  }, []);

  return (
    <div ref={overlay} className="viewer-overlay" role="dialog" aria-modal="true" aria-label={caption}>
      <div className="viewer-bar">
        <span className="viewer-caption truncate" title={caption}>{caption}</span>
        <div className="viewer-tools">
          <button className="btn small" onClick={() => zoomCentre(1.25)} title="zoom in (+)" aria-label="zoom in">+</button>
          <button className="btn small" onClick={() => zoomCentre(0.8)} title="zoom out (−)" aria-label="zoom out">−</button>
          <button className="btn small" onClick={fit} title="see it whole (0)">Fit</button>
          <span className="viewer-scale">{Math.round(view.k * 100)}%</span>
          {content.kind === "svg" && (
            <>
              <button className="btn small" onClick={() => exportAs("svg")} title="save the drawing as SVG">SVG</button>
              <button className="btn small" onClick={() => exportAs("png")} title="save the drawing as PNG, at twice its size">PNG</button>
              <button className="btn small" onClick={() => exportAs("mmd")} title="save the Mermaid source">.mmd</button>
            </>
          )}
          {content.kind === "img" && (
            <a className="btn small" href={content.src} download={content.name}><Icon name="download" size={14} /> Save</a>
          )}
          <button ref={closeButton} className="btn small primary" onClick={onClose} title="close (Esc)"><Icon name="x" size={14} /> Close</button>
        </div>
      </div>
      <div className="viewer-stage" ref={stage} onPointerDown={onPointerDown}
        onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
        <div className="viewer-layer" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})`,
          width: natural.w || undefined, height: natural.h || undefined }}>
          {content.kind === "svg" ? (
            <div dangerouslySetInnerHTML={{ __html: content.markup }} />
          ) : (
            <img src={content.src} alt={content.name} draggable={false}
              onLoad={(event) => setNatural({ w: event.currentTarget.naturalWidth, h: event.currentTarget.naturalHeight })} />
          )}
        </div>
      </div>
      <div className="viewer-hint">drag or arrows to move · scroll or pinch to zoom · 0 fits · Esc closes</div>
    </div>
  );
}
