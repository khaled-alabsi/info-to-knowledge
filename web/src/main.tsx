import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import AuthGate from "./components/AuthGate";
import "./styles.css";

// A page opened before the last `npm run build` asks for code files that build deleted (Mermaid, the highlighter),
// and the diagrams stay as text. Vite reports that failed load; the page then reloads once to fetch the new build.
const RELOADED_KEY = "i2k-reloaded-for-build";
let reloadPage = () => window.location.reload();

// Test seam: a browser's `location.reload` cannot be replaced, so a test swaps this one; returns the one it replaced.
export function replaceReloadPage(next: () => void): () => void {
  const previous = reloadPage;
  reloadPage = next;
  return previous;
}

window.addEventListener("vite:preloadError", (event) => {
  try {
    if (sessionStorage.getItem(RELOADED_KEY)) return;
    sessionStorage.setItem(RELOADED_KEY, "1");
  } catch {
    // storage unavailable: no reload, since the reloaded page could not tell it had reloaded and would reload for
    // ever; the failed load stands, and the diagrams stay as text
    return;
  }
  event.preventDefault();
  reloadPage();
});
window.addEventListener("load", () => {
  window.setTimeout(() => {
    try {
      sessionStorage.removeItem(RELOADED_KEY);
    } catch {
      // storage unavailable: nothing to clear
    }
  }, 10_000);
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AuthGate>{(account, onSignOut) => <App account={account} onSignOut={onSignOut} />}</AuthGate>
  </StrictMode>,
);
