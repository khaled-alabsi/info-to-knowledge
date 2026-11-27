import { useEffect, useState } from "react";

// Where the page was, kept in one cookie so a refresh returns to it: the sidebar tab, the open run or job, the run's
// tab, the artifact group and file, the inbox lists that are open. One read and one save for the whole page; a value
// that cannot be read is no value, never an error.
const COOKIE_NAME = "i2k-view";
const MAX_AGE_S = 365 * 24 * 3600;

export type ViewState = Record<string, unknown>;

export function readViewState(): ViewState {
  try {
    const entry = document.cookie.split("; ").find((part) => part.startsWith(`${COOKIE_NAME}=`));
    if (!entry) return {};
    const value = JSON.parse(decodeURIComponent(entry.slice(COOKIE_NAME.length + 1)));
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

export function saveViewValue(key: string, value: unknown): void {
  try {
    const next = { ...readViewState(), [key]: value };
    if (value === undefined || value === null) delete next[key];
    document.cookie = `${COOKIE_NAME}=${encodeURIComponent(JSON.stringify(next))}; path=/; max-age=${MAX_AGE_S}; SameSite=Lax`;
  } catch {
    // cookies blocked: the page works, it only forgets where it was
  }
}

// Whether a saved value is of the fallback's kind: a string for a string, a list for a list. A null fallback takes any.
function isSameKind(value: unknown, fallback: unknown): boolean {
  if (fallback === null) return true;
  if (Array.isArray(fallback)) return Array.isArray(value);
  return typeof value === typeof fallback;
}

// A saved value, or the fallback when there is none or when an older page wrote what this one cannot use: a value of
// another kind, or one `isValid` refuses (a tab that is gone)
export function readViewValue<T>(key: string, fallback: T, isValid: (value: unknown) => boolean = () => true): T {
  const value = readViewState()[key];
  return value !== undefined && isSameKind(value, fallback) && isValid(value) ? (value as T) : fallback;
}

// A piece of state that starts from the cookie and is saved back on every change.
export function usePersistentState<T>(key: string, initial: T,
  isValid?: (value: unknown) => boolean): [T, (value: T | ((previous: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => readViewValue(key, initial, isValid));
  useEffect(() => {
    saveViewValue(key, value);
  }, [key, value]);
  return [value, setValue];
}
