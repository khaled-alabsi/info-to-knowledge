import { describeError } from "./api";

// What a rendered markdown document gets after its HTML is in the page: its diagrams drawn and its code coloured, the
// same in the reading mode, the worker view and the instructions editor.

const isDark = () => window.matchMedia("(prefers-color-scheme: dark)").matches;

// Draws every ```mermaid fence in place, colours every other code fence and gives it a Copy button. Mermaid and the
// highlighter load only when a document holds what they draw.
export async function enhanceDocument(container: HTMLElement): Promise<void> {
  const fences = [...container.querySelectorAll("code.language-mermaid")];
  const boxes = fences.map((code, index) => {
    const figure = document.createElement("div");
    figure.className = "figure diagram";
    const box = document.createElement("pre");
    box.className = "mermaid";
    box.textContent = code.textContent ?? "";
    // Kept apart from the drawing: Mermaid replaces the text with an SVG, and the .mmd export needs the source.
    box.dataset.src = code.textContent ?? "";
    box.dataset.index = String(index + 1);
    const expand = document.createElement("button");
    expand.className = "btn small figure-expand";
    expand.type = "button";
    expand.dataset.action = "expand";
    expand.textContent = "⤢ Expand";
    expand.title = "open full screen: drag to move, scroll or pinch to zoom";
    figure.append(box, expand);
    code.closest("pre")?.replaceWith(figure);
    return box;
  });
  // a picture opens full screen from the keyboard too: it takes the focus and answers Enter, as a button does
  container.querySelectorAll<HTMLImageElement>("img").forEach((image) => {
    if (image.closest("a")) return;
    image.tabIndex = 0;
    image.setAttribute("role", "button");
    image.setAttribute("aria-label", `open ${image.alt || "the picture"} full screen`);
  });
  if (boxes.length) {
    let mermaid: typeof import("mermaid").default;
    try {
      mermaid = (await import("mermaid")).default;
    } catch {
      boxes.forEach((box) => {
        box.classList.add("mermaid-error");
        box.textContent = `The diagram drawer did not load (the page is older than the last build): reload the page.\n\n${box.dataset.src}`;
      });
      return;
    }
    mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: isDark() ? "dark" : "default" });
    for (const box of boxes) {
      try {
        await mermaid.run({ nodes: [box] });
      } catch (error) {
        box.classList.add("mermaid-error");
        box.textContent = `This diagram could not be drawn: ${describeError(error)}\n\n${box.dataset.src}`;
      }
    }
  }
  const blocks = [...container.querySelectorAll("pre > code")].filter((code) => !code.classList.contains("language-mermaid"));
  if (blocks.length) {
    const hljs = (await import("highlight.js/lib/common")).default;
    blocks.forEach((code) => {
      hljs.highlightElement(code as HTMLElement);
      const pre = code.parentElement;
      if (!pre || pre.querySelector(".copy-code")) return;
      pre.classList.add("code-block");
      const copy = document.createElement("button");
      copy.className = "btn small copy-code";
      copy.type = "button";
      copy.dataset.action = "copy";
      copy.textContent = "Copy";
      pre.append(copy);
    });
  }
}

// When a version was kept: its UTC stamp (`20261106T190700.123456`), read back as a time a person recognises.
export function formatStamp(stamp: string): string {
  const parts = stamp.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})/);
  if (!parts) return stamp;
  const when = new Date(Date.UTC(+parts[1], +parts[2] - 1, +parts[3], +parts[4], +parts[5], +parts[6]));
  return when.toLocaleString();
}
