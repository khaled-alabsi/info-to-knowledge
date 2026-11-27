import Icon from "./Icon";

export interface Toast {
  id: number;
  text: string;
  isError: boolean;
}

// What each action did, in the corner: a message closes by itself after a few seconds, an error only on its ×.
export default function Toasts({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  return (
    <div className="toasts" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast${toast.isError ? " error" : ""}`}>
          {/* An error's mark is the warning triangle: a red × beside the close × read as a second close button */}
          <Icon name={toast.isError ? "alert" : "check"} />
          <span>{toast.text}</span>
          <button className="btn ghost icon-only" onClick={() => onDismiss(toast.id)} aria-label="dismiss">
            <Icon name="x" size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
