import { useEffect, useRef, useState } from "react";
import { describeError } from "../api";
import { TagInput, mergeTags } from "./TagInput";

// The tags given to a run or to an inbox video before its run: each a pill with a button that takes it off, and a box
// that suggests the tags in use and where Enter adds one (a comma adds several). Every change is saved at once by
// `save`, which throws with the server's words when it is refused; a refused change is taken back off the screen.
export function RunTags({ tags, save, onChanged = () => undefined }: {
  tags: string[];
  save: (tags: string[]) => Promise<void>;
  onChanged?: () => void;
}) {
  const [shown, setShown] = useState(tags);
  const [error, setError] = useState("");
  // The box opens from a small "+ tag" pill beside the tags, and closes back into it when left empty (the owner)
  const [isAdding, setIsAdding] = useState(false);
  const addButton = useRef<HTMLButtonElement>(null);
  useEffect(() => setShown(tags), [tags.join("\u0000")]);

  const change = async (next: string[]) => {
    const before = shown;
    setShown(next);
    try {
      await save(next);
      setError("");
    } catch (problem) {
      setShown(before);
      setError(describeError(problem));
    }
    onChanged();
  };
  const add = (typed: string[]) => {
    const next = mergeTags(shown, typed);
    if (next.length > shown.length) void change(next);
  };

  return (
    <div className="run-tags" role="group" aria-label="tags">
      {shown.map((tag) => (
        <span key={tag} className="pill tag">
          {tag}
          {/* a removed tag takes its button with it: the focus goes on to "+ tag" rather than to nowhere */}
          <button className="tag-remove" aria-label={`remove tag ${tag}`} onClick={() => {
            void change(shown.filter((other) => other !== tag));
            addButton.current?.focus();
          }}>×</button>
        </span>
      ))}
      {isAdding
        ? <TagInput given={shown} onAdd={add} onLeave={(isByKeyboard) => {
          setIsAdding(false);
          // left by Escape, the keyboard comes back to "+ tag"; left by a click elsewhere, the focus stays where it went
          if (isByKeyboard) window.requestAnimationFrame(() => addButton.current?.focus());
        }} />
        : <button ref={addButton} className="pill tag-add" aria-label="add a tag" onClick={() => setIsAdding(true)}>+ tag</button>}
      {error && <small className="error">{error}</small>}
    </div>
  );
}
