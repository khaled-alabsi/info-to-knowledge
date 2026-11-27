import { useEffect, useId, useState } from "react";
import type { KeyboardEvent } from "react";
import { fetchJson } from "../api";
import type { KnownTags } from "../api";

// The tags runs have and the profiles' names, fetched once per page and kept: the server takes seconds to gather them,
// and a box opened before they came showed no list for that long, with nothing saying why (the owner). The page asks
// for them as it loads (`App`); a failed fetch rejects, and the next box asks again.
let knownTags: Promise<string[]> | null = null;

export function loadKnownTags(): Promise<string[]> {
  knownTags ??= fetchJson<KnownTags>("/tags")
    .then((known) => [...known.tags, ...known.profiles.flatMap((profile) => [profile.name, ...profile.aliases])])
    .catch((error: unknown) => {
      knownTags = null;
      throw error;
    });

  return knownTags;
}

// A tag just given is known at once, without asking the server again (with no list yet, it starts one)
export function rememberTags(tags: string[]): void {
  knownTags = (knownTags ?? Promise.resolve([])).catch(() => [] as string[])
    .then((known) => [...known, ...tags.filter((tag) => !known.includes(tag))]);
}

// For tests: the next box asks the server again
export function forgetKnownTags(): void {
  knownTags = null;
}

// The suggestions for what is typed: tags that start with it first, then tags that hold it, letter case ignored; the
// tags already given are left out, and an empty box suggests nothing.
export function listSuggestions(candidates: string[], given: string[], typed: string, limit = 8): string[] {
  const text = typed.trim().toLowerCase();
  if (!text) return [];
  const taken = new Set(given.map((tag) => tag.toLowerCase()));
  const unique = [...new Map(candidates.map((tag) => [tag.toLowerCase(), tag])).values()]
    .filter((tag) => !taken.has(tag.toLowerCase()));
  const starting = unique.filter((tag) => tag.toLowerCase().startsWith(text));
  const holding = unique.filter((tag) => !tag.toLowerCase().startsWith(text) && tag.toLowerCase().includes(text));

  return [...starting, ...holding].slice(0, limit);
}

// Tag lists joined into one, each tag once whatever its letter case, the first spelling kept: the server keeps one tag
// per spelling too, so two lists that differ only in case are one choice
export function mergeTags(...lists: string[][]): string[] {
  const merged = new Map<string, string>();
  for (const tag of lists.flat()) if (!merged.has(tag.toLowerCase())) merged.set(tag.toLowerCase(), tag);
  return [...merged.values()];
}

// The box a tag is typed into, suggesting the tags runs already have and the ingesting profiles' names and the tags
// that pick them (`GET /api/tags`). Arrow keys mark a suggestion, Enter or Tab takes it, Escape closes the list; Enter
// with nothing marked adds what was typed, a comma splitting it into several.
export function TagInput({ given, onAdd, className = "tag-input", onLeave, options, label = "add a tag" }: {
  given: string[];
  onAdd: (tags: string[]) => void;
  className?: string;
  // Called on Escape, or on blur with the box empty: a box opened from a button closes back into it
  onLeave?: (isByKeyboard: boolean) => void;
  // A fixed list to choose from in place of every known tag (a filter): Enter takes the marked or first suggestion,
  // never what was typed, since a tag no run has filters nothing
  options?: string[];
  label?: string;
}) {
  const [draft, setDraft] = useState("");
  const [fetched, setFetched] = useState<string[]>([]);
  const candidates = options ?? fetched;
  const setCandidates = setFetched;
  const [marked, setMarked] = useState(-1);
  const [isOpen, setIsOpen] = useState(false);
  // the tags in use could not be read: the box still takes what is typed, and says it has nothing to suggest
  const [isSuggestionsLost, setIsSuggestionsLost] = useState(false);
  useEffect(() => {
    if (options) return undefined;
    let isLive = true;
    loadKnownTags().then((known) => { if (isLive) setCandidates(known); })
      .catch(() => { if (isLive) setIsSuggestionsLost(true); });
    return () => {
      isLive = false;
    };
  }, [options]);
  const suggestions = isOpen ? listSuggestions(candidates, given, draft) : [];

  const take = (tags: string[]) => {
    const added = tags.map((tag) => tag.trim().replace(/\s+/g, " ")).filter(Boolean);
    setDraft("");
    setMarked(-1);
    if (added.length && options) {
      onAdd(added);
    } else if (added.length) {
      rememberTags(added);
      setCandidates((known) => [...known, ...added.filter((tag) => !known.includes(tag))]);
      onAdd(added);
    }
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown" && suggestions.length) {
      event.preventDefault();
      setMarked((marked + 1) % suggestions.length);
    } else if (event.key === "ArrowUp" && suggestions.length) {
      event.preventDefault();
      setMarked(marked <= 0 ? suggestions.length - 1 : marked - 1);
    } else if (event.key === "Escape") {
      setIsOpen(false);
      setMarked(-1);
    } else if (event.key === "Tab" && marked >= 0 && suggestions[marked]) {
      event.preventDefault();
      take([suggestions[marked]]);
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (options) take(suggestions[Math.max(marked, 0)] ? [suggestions[Math.max(marked, 0)]] : []);
      else take(marked >= 0 && suggestions[marked] ? [suggestions[marked]] : draft.split(","));
    }
  };

  // each box's own list ids: two boxes can show at once (the run's header and the Run panel)
  const listId = useId();

  return (
    <span className="tag-entry">
      <input type="text" className={className} role="combobox" aria-label={label} placeholder={label}
        aria-expanded={suggestions.length > 0} aria-controls={listId} aria-autocomplete="list"
        aria-activedescendant={marked >= 0 && suggestions[marked] ? `${listId}-${marked}` : undefined}
        value={draft} autoFocus={Boolean(onLeave)}
        onKeyDown={(event) => {
          if (event.key === "Escape" && onLeave) onLeave(true);
          else onKeyDown(event);
        }}
        onBlur={() => {
          setIsOpen(false);
          if (onLeave && !draft.trim()) onLeave(false);
        }}
        onChange={(event) => {
          setDraft(event.target.value);
          setIsOpen(true);
          setMarked(-1);
        }} />
      {suggestions.length > 0 && (
        <ul className="tag-suggestions" id={listId} role="listbox" aria-label="tag suggestions">
          {suggestions.map((tag, at) => (
            <li key={tag} id={`${listId}-${at}`} role="option" aria-selected={at === marked} className={at === marked ? "on" : ""}
              onMouseDown={(event) => {
                // Taken before the box loses focus, which would close the list
                event.preventDefault();
                take([tag]);
              }}>
              {tag}
            </li>
          ))}
        </ul>
      )}
      {isSuggestionsLost && isOpen && draft.trim() && <small className="muted">the tags in use could not be read: no suggestions</small>}
    </span>
  );
}
