import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import Icon from "./Icon";

// A modal window over the page; Escape or the close button ends it.
export default function Dialog({ title, onClose, children }: { title: ReactNode; onClose: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  // the dialog is named by its title, so a screen reader says more than "dialog"
  const titleId = useId();

  // the focus goes back to what opened the dialog, however it closes: removed while open, a dialog leaves it nowhere
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    return () => before?.focus();
  }, []);

  // drawn at the page's top level: inside a panel the phone layout hides, an open dialog was hidden with it while the
  // page under it stayed inert
  return createPortal(
    // Escape is the browser's cancel, whose own action closes the dialog and fires close: close alone reaches onClose once
    <dialog ref={dialog} className="dialog" aria-labelledby={titleId} onClose={onClose}>
      <header>
        <h3 id={titleId}>{title}</h3>
        <button className="btn ghost icon-only" onClick={onClose} aria-label="close">
          <Icon name="x" />
        </button>
      </header>
      <div className="dialog-body">{children}</div>
    </dialog>,
    document.body,
  );
}
