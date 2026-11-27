import { useState } from "react";
import { copyText } from "../clipboard";
import Icon from "./Icon";

// What the last copy did, shown on the button for a moment
const COPY_LABELS = { "": "Copy", copied: "Copied", failed: "Copy failed" } as const;
const COPY_SHOWN_MS = 1500;

// A Copy button: it puts `getText`'s text on the clipboard and says so. A text that cannot be read, or a clipboard the
// browser refuses (the page not focused, plain http on another host), shows "Copy failed" on the button.
export default function CopyButton({ getText, title, className = "btn ghost small", isDisabled = false }: {
  getText: () => string | Promise<string>;
  title?: string;
  className?: string;
  isDisabled?: boolean;
}) {
  const [outcome, setOutcome] = useState<keyof typeof COPY_LABELS>("");
  const copy = async () => {
    try {
      await copyText(await getText());
      setOutcome("copied");
    } catch {
      setOutcome("failed");
    }
    window.setTimeout(() => setOutcome(""), COPY_SHOWN_MS);
  };
  return (
    <button type="button" className={className} title={title} disabled={isDisabled} onClick={() => void copy()}>
      <Icon name={outcome === "copied" ? "check" : "copy"} size={14} /> {COPY_LABELS[outcome]}
    </button>
  );
}
