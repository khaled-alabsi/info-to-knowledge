// Tab and Shift+Tab kept inside an element that covers the page (a viewer, the reading mode): past its last control
// the focus comes back to its first, so the keyboard never reaches the controls hidden under it.
const FOCUSABLE = "a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea, [tabindex]:not([tabindex='-1'])";

export function keepTabInside(event: KeyboardEvent, container: HTMLElement | null): void {
  if (event.key !== "Tab" || !container) return;
  const controls = [...container.querySelectorAll<HTMLElement>(FOCUSABLE)];
  if (!controls.length) return;
  const first = controls[0];
  const last = controls[controls.length - 1];
  const isInside = container.contains(document.activeElement);
  if (event.shiftKey && (document.activeElement === first || !isInside)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (document.activeElement === last || !isInside)) {
    event.preventDefault();
    first.focus();
  }
}
