import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import { createPortal } from "react-dom";
import Icon from "./Icon";

const GAP_PX = 6;
// the controls the keyboard can reach inside a panel
const FOCUSABLE = "button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea, [tabindex]:not([tabindex='-1'])";
const EDGE_PX = 12;

// A button that opens a panel under it (above it when the space below runs out; on the larger side, scrolling inside
// itself, when it fits on neither). The panel is drawn on the page's top layer, so a scrolling list around the button
// never cuts it off. Opened, its first control takes the focus, so the keyboard reaches it; it closes on a click
// outside, on Escape (the focus going back to the button), on scroll of the page while the focus is not in it, or when
// its content calls `close`.
export function Dropdown({
  label,
  icon,
  variant = "",
  align = "left",
  title,
  ariaLabel,
  panelClassName = "",
  popup = "dialog",
  children,
}: {
  label?: ReactNode;
  icon?: string;
  variant?: string;
  align?: "left" | "right";
  title?: string;
  // the button's name for a screen reader, when its words alone do not say what it chooses (a setting's value)
  ariaLabel?: string;
  // a class added to the panel, as a wider panel for long choices
  panelClassName?: string;
  // what the panel is to a screen reader: a menu of actions, or a dialog of choices or help
  popup?: "menu" | "dialog";
  // `close` leaves the focus where it is; `closeAndRefocus` gives it back to the button, for an action that takes away
  // the panel and the control that had the focus
  children: (close: () => void, closeAndRefocus: () => void) => ReactNode;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [place, setPlace] = useState<CSSProperties>({ visibility: "hidden" });
  const button = useRef<HTMLButtonElement>(null);
  // a panel with no title of its own is named by its button's words ("Run", "Run again")
  const buttonId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setIsOpen(false), []);
  const closeAndRefocus = useCallback(() => {
    setIsOpen(false);
    button.current?.focus();
  }, []);

  const measure = useCallback(() => {
    const anchor = button.current?.getBoundingClientRect();
    const box = panel.current?.getBoundingClientRect();
    if (!anchor || !box || !panel.current) return;
    // the panel's whole height, not the height a cap set by an earlier measure left it
    const height = Math.max(box.height, panel.current.scrollHeight);
    const spaceBelow = window.innerHeight - anchor.bottom - GAP_PX - EDGE_PX;
    const spaceAbove = anchor.top - GAP_PX - EDGE_PX;
    const isAbove = height > spaceBelow && spaceAbove > spaceBelow;
    const room = isAbove ? spaceAbove : spaceBelow;
    const top = isAbove ? Math.max(EDGE_PX, anchor.top - Math.min(height, room) - GAP_PX) : anchor.bottom + GAP_PX;
    const wanted = align === "right" ? anchor.right - box.width : anchor.left;
    const left = Math.min(Math.max(EDGE_PX, wanted), window.innerWidth - box.width - EDGE_PX);
    setPlace({ top, left, maxHeight: height > room ? room : undefined, visibility: "visible" });
  }, [align]);

  useLayoutEffect(() => {
    if (isOpen) measure();
    else setPlace({ visibility: "hidden" });
  }, [isOpen, measure]);

  // Opened from the keyboard, the panel's first control takes the focus once it is placed: drawn at the page's end, the
  // panel is out of reach of Tab. Opened by a click, the focus stays where it was and a scroll of the page closes it.
  const isOpenedByKeyboard = useRef(false);
  const isPlaced = place.visibility === "visible";
  useEffect(() => {
    if (!isOpen || !isPlaced || !isOpenedByKeyboard.current) return;
    // a panel with no control of its own (a help text) takes the focus itself, so it is read
    (panel.current?.querySelector<HTMLElement>(FOCUSABLE) ?? panel.current)?.focus({ preventScroll: true });
  }, [isOpen, isPlaced]);

  useEffect(() => {
    if (!isOpen) return;
    const onPointer = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!button.current?.contains(target) && !panel.current?.contains(target)) close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        // the panel alone closes: a dialog round it stays open
        event.preventDefault();
        event.stopPropagation();
        closeAndRefocus();
        return;
      }
      // Tab past the panel's last control, or Shift+Tab before its first, closes it and goes back to its button: drawn
      // at the page's end, the panel would otherwise hand the focus to the end or the top of the page
      if (event.key !== "Tab" || !panel.current?.contains(document.activeElement)) return;
      const controls = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      const isLeaving = controls.length === 0 || (event.shiftKey ? document.activeElement === controls[0]
        : document.activeElement === controls[controls.length - 1]);
      if (!isLeaving) return;
      if (event.shiftKey) event.preventDefault();
      closeAndRefocus();
    };
    // Tabbing through the panel scrolls the page to the focused control: the panel follows it rather than closing
    const onScroll = (event: Event) => {
      if (panel.current?.contains(event.target as Node)) return;
      if (panel.current?.contains(document.activeElement)) measure();
      else close();
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", measure);
    const watcher = new ResizeObserver(measure);
    if (panel.current) watcher.observe(panel.current);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", measure);
      watcher.disconnect();
    };
  }, [isOpen, close, closeAndRefocus, measure]);

  return (
    <>
      <button ref={button} id={buttonId} className={`btn ${variant}${isOpen ? " active" : ""}`} onClick={(event) => {
          // a click the keyboard made (Enter or Space on the button) counts no mouse presses
          isOpenedByKeyboard.current = event.detail === 0;
          setIsOpen(!isOpen);
        }}
        aria-haspopup={popup} aria-expanded={isOpen} title={title} aria-label={ariaLabel}>
        {icon && <Icon name={icon} />}
        {label && <span className="btn-label">{label}</span>}
        {label && <Icon name="chevron" size={14} />}
      </button>
      {isOpen && createPortal(
        <div ref={panel} className={`dropdown-panel ${panelClassName}`.trim()} style={place} tabIndex={-1}
          {...(popup === "dialog" ? { role: "dialog", ...(title ? { "aria-label": title } : { "aria-labelledby": buttonId }) } : {})}>{children(close, closeAndRefocus)}</div>,
        // inside an open modal dialog the page's body lies under it and takes no click, so the panel goes into that
        // dialog (a Workflow speed menu opened in the Run settings dialog could not be clicked)
        button.current?.closest("dialog") ?? document.body,
      )}
    </>
  );
}

export interface MenuItem {
  label: string;
  hint?: string;
  icon?: string;
  isDanger?: boolean;
  isDisabled?: boolean;
  // a heading the item stands under; items of one group follow each other, the heading drawn once above them
  group?: string;
  // an item that turns something on or off: drawn with a small switch on its right, and read as a checkbox
  isChecked?: boolean;
  // the menu stays open when it is chosen: a switch that changes what the menu's other items do
  isKeptOpen?: boolean;
  onSelect: () => void;
}

// The line at the top of a menu or panel saying what all of it does; `help`, when given, is behind a "?" at the line's
// end and opens under it, inside the menu (the owner: a menu's help belongs in the menu, never beside it)
export function PanelNote({ text, help }: { text: string; help?: ReactNode }) {
  const [isHelpOpen, setIsHelpOpen] = useState(false);
  return (
    <div className="menu-note">
      <div className="menu-note-line">
        <span>{text}</span>
        {help && (
          <button type="button" className="btn ghost icon-only tiny" aria-label="explain this menu" aria-expanded={isHelpOpen}
            onClick={() => setIsHelpOpen(!isHelpOpen)}>
            <Icon name="help" />
          </button>
        )}
      </div>
      {help && isHelpOpen && <div className="menu-help" role="note">{help}</div>}
    </div>
  );
}

// A dropdown of actions, one line each; `note` is one line of help above them, for what every item has in common, and
// `help` the longer explanation behind its "?".
export function Menu(props: {
  label?: ReactNode;
  icon?: string;
  variant?: string;
  align?: "left" | "right";
  title?: string;
  note?: string;
  help?: ReactNode;
  items: MenuItem[];
}) {
  const { items, note, help, ...button } = props;
  return (
    <Dropdown {...button} popup="menu">
      {(_close, closeAndRefocus) => (
        <ul className="menu" role="menu" onKeyDown={(event) => {
          // the arrow keys move between the menu's items, as in any menu
          if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
          event.preventDefault();
          const entries = [...event.currentTarget.querySelectorAll<HTMLElement>("[role='menuitem']")];
          const at = entries.indexOf(document.activeElement as HTMLElement);
          const step = event.key === "ArrowDown" ? 1 : -1;
          entries[(at + step + entries.length) % entries.length]?.focus();
        }}>
          {note && <li role="none"><PanelNote text={note} help={help} /></li>}
          {items.map((item, index) => [
            item.group && item.group !== items[index - 1]?.group && (
              <li key={`group:${item.group}`} className="menu-heading menu-group" role="none">{item.group}</li>
            ),
            <li key={item.label} role="none">
              {/* a disabled item stays reachable, so its reason (the hint) is read; it does nothing when chosen */}
              <button role={item.isChecked === undefined ? "menuitem" : "menuitemcheckbox"}
                aria-checked={item.isChecked} className={item.isDanger ? "danger" : ""} aria-disabled={item.isDisabled || undefined}
                onClick={() => {
                  if (item.isDisabled) return;
                  if (item.isKeptOpen) {
                    item.onSelect();
                    return;
                  }
                  // the focus goes back to the menu's button before the item acts, so a dialog the item opens gives it
                  // back there when it closes, never to the page
                  closeAndRefocus();
                  item.onSelect();
                }}>
                {item.icon && <Icon name={item.icon} />}
                <span className="menu-text">
                  <span>{item.label}</span>
                  {item.hint && <small>{item.hint}</small>}
                </span>
                {item.isChecked !== undefined && <span className="menu-switch" aria-hidden="true" />}
              </button>
            </li>,
          ])}
        </ul>
      )}
    </Dropdown>
  );
}
