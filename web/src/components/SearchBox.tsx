import { useRef } from "react";
import Icon from "./Icon";

// A search box with an X that empties it and gives the focus back to the box; the X shows only while there is text.
export default function SearchBox({ value, onChange, label, placeholder, isFocused = false }: { value: string;
  onChange: (value: string) => void; label: string; placeholder: string;
  // true where the box appears because the user asked for it, so typing starts at once
  isFocused?: boolean }) {
  const input = useRef<HTMLInputElement>(null);

  return (
    <div className="search-box">
      <input ref={input} type="search" className="search" autoFocus={isFocused} aria-label={label} placeholder={placeholder} value={value}
        onChange={(event) => onChange(event.target.value)} />
      {value && (
        <button type="button" className="btn ghost icon-only small search-clear" aria-label="clear the search"
          title="clear the search" onClick={() => { onChange(""); input.current?.focus(); }}><Icon name="x" /></button>
      )}
    </div>
  );
}
