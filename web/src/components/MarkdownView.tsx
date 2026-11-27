import DOMPurify from "dompurify";
import { marked } from "marked";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent } from "react";
import { buildDownloadUrl, buildFileUrl, describeError, fetchJson, saveMarkdown, type MarkdownVersion } from "../api";
import { copyText } from "../clipboard";
import Dialog from "./Dialog";
import FigureViewer from "./FigureViewer";
import type { FigureContent } from "./FigureViewer";
import Icon from "./Icon";
import Spinner from "./Spinner";
import * as focusTrap from "../focusTrap";
import { enhanceDocument, formatStamp } from "../markdownEnhance";

// `---` lines around `key: value` lines at the very top of a document: its metadata, shown as a table.
const FRONT_MATTER = /^---\r?\n([\s\S]*?)\r?\n---\r?\n/;
// One entry per block the editor committed, the oldest dropped: an afternoon of editing holds this many strings, not
// one per keystroke.
const UNDO_KEPT = 100;
// A document drawn in parts of about this many characters, the next part added on request: a 6 MB document with 2,732
// figures, drawn whole, ran a phone's browser out of memory and Safari reloaded the page again and again
const PART_CHARS = 300_000;

// The start of `body` up to about `limit` characters, cut where a block ends (a blank line), never inside one
export function cutAtBlockEnd(body: string, limit: number): string {
  if (body.length <= limit) return body;
  const end = body.lastIndexOf("\n\n", limit);
  return body.slice(0, end > limit / 2 ? end : limit);
}

function splitFrontMatter(text: string): { raw: string; fields: [string, string][]; body: string } {
  const match = text.match(FRONT_MATTER);
  if (!match) return { raw: "", fields: [], body: text };
  const fields = match[1].split(/\r?\n/)
    .map((line) => line.match(/^([\w-]+):\s*(.*)$/))
    .filter((found): found is RegExpMatchArray => found !== null)
    .map((found) => [found[1], readFieldValue(found[2])] as [string, string]);
  return { raw: match[0], fields, body: text.slice(match[0].length) };
}

// A field's value as a reader reads it: quotes taken off, and a list written `["A", "B"]` as its items with commas, so
// an empty one shows as no value (the owner saw "[]" beside Speakers on a recording where no name was said).
function readFieldValue(value: string): string {
  if (/^\[.*\]$/.test(value)) {
    try {
      const items: unknown = JSON.parse(value);
      if (Array.isArray(items)) return items.map(String).join(", ");
    } catch {
      // not a list written as JSON: shown as written
    }
  }
  return value.replace(/^["']|["']$/g, "");
}

function escapeText(text: string): string {
  const box = document.createElement("div");
  box.textContent = text;
  return box.innerHTML;
}

// Every top-level block carries the line it started on and the markdown it was made from: the line lets a link land
// on a heading, and the markdown is what the editor opens in a textarea and what a save reassembles the file from.
function renderBlocks(body: string): string {
  let tokens: ReturnType<typeof marked.lexer>;
  try {
    tokens = marked.lexer(body);
  } catch {
    return marked.parse(body, { async: false }) as string;
  }
  let line = 1;
  let html = "";
  for (const token of tokens) {
    const at = line;
    line += (token.raw.match(/\n/g) ?? []).length;
    html += `<div data-line="${at}" data-src="${encodeURIComponent(token.raw)}">${renderOneBlock(token.raw)}</div>`;
  }
  return html;
}

function renderOneBlock(source: string): string {
  try {
    return marked.parser(marked.lexer(source), { async: false }) as string;
  } catch {
    return `<pre>${escapeText(source)}</pre>`;
  }
}

// The anchor a heading gets, as GitHub writes it, so a contents list written for GitHub jumps to it here too.
function buildSlug(text: string): string {
  return text.trim().toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, "").replace(/\s/g, "-");
}

// Headings get their anchors; relative images and links point next to the document in the run folder, or into
// `linkFolder` when given (an earlier version kept in phase 7's folder links the figures the published folder holds);
// links that leave the document open in a new tab, and `#` links stay in it.
function prepareHtml(html: string, runBase: string, documentPath: string, linkFolder?: string): string {
  const ownFolder = documentPath.includes("/") ? documentPath.slice(0, documentPath.lastIndexOf("/") + 1) : "";
  const folder = linkFolder ?? ownFolder;
  const page = new DOMParser().parseFromString(html, "text/html");
  const isRelative = (target: string) => target && !/^([a-z]+:|\/|#)/i.test(target);
  const used = new Map<string, number>();
  page.querySelectorAll("h1, h2, h3, h4, h5, h6").forEach((heading) => {
    const slug = buildSlug(heading.textContent ?? "");
    const count = used.get(slug) ?? 0;
    used.set(slug, count + 1);
    heading.id = count ? `${slug}-${count}` : slug;
  });
  page.querySelectorAll("img").forEach((image) => {
    const source = image.getAttribute("src") ?? "";
    if (isRelative(source)) image.setAttribute("src", buildFileUrl(runBase, folder + decodeURI(source)));
    image.setAttribute("loading", "lazy");
  });
  page.querySelectorAll("a").forEach((link) => {
    const target = link.getAttribute("href") ?? "";
    if (target.startsWith("#")) return;
    if (isRelative(target)) link.setAttribute("href", buildFileUrl(runBase, folder + decodeURI(target)));
    link.setAttribute("target", "_blank");
    link.setAttribute("rel", "noreferrer");
  });
  return page.body.innerHTML;
}

// A document's own contents list: the list right after a heading named "Contents" or "Table of contents". Reading
// mode pins it in a column on the left and leaves it out of the text beside it.
function splitContents(html: string): { contents: string; rest: string } {
  const page = new DOMParser().parseFromString(html, "text/html");
  const heading = [...page.querySelectorAll("h1, h2, h3")]
    .find((candidate) => /^(table of )?contents$/i.test((candidate.textContent ?? "").trim()));
  if (!heading) return { contents: "", rest: html };
  // The heading sits in its own block, so the list is in the first block after it that holds anything (the blank
  // lines between them are blocks of their own); without blocks it is the next element.
  const block = heading.closest("[data-line]");
  let after = block ? block.nextElementSibling : null;
  while (after && (after.textContent ?? "").trim() === "") after = after.nextElementSibling;
  const list = block ? after?.querySelector("ul, ol") ?? after : heading.nextElementSibling;
  if (!list || !["UL", "OL"].includes(list.tagName)) return { contents: "", rest: html };
  const contents = list.outerHTML;
  heading.remove();
  list.remove();
  return { contents, rest: page.body.innerHTML };
}

// One markdown file of the run folder, rendered; the HTML is cleaned before it reaches the page. In reading mode the
// lock opens an editor in the rendered document: the block under the caret shows its markdown, the rest stays drawn.
export default function MarkdownView({ runBase, path, linkFolder, isReadingAsked = false, onReadingStarted, liveMs }: {
  runBase: string;
  path: string;
  // the folder, ending in "/", the document's relative links are read from, when it is not the document's own
  linkFolder?: string;
  // opened full screen at once: the detail view's Open document
  isReadingAsked?: boolean;
  onReadingStarted?: () => void;
  // read again this often while shown, for a document a run still writes (a phase 7 part's draft, the owner: "see
  // the part file as it updated in live"); a document being edited is never replaced
  liveMs?: number;
}) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [isSource, setIsSource] = useState(false);
  const [isReading, setIsReading] = useState(false);
  // On a narrow screen the contents list stands above the text, folded under its title until the reader opens it.
  const [isContentsOpen, setIsContentsOpen] = useState(false);
  useEffect(() => {
    if (!isReadingAsked) return;
    setIsReading(true);
    onReadingStarted?.();
  }, [isReadingAsked, onReadingStarted]);
  const [isUnlocked, setIsUnlocked] = useState(false);
  const [isDirty, setIsDirty] = useState(false);
  const [note, setNote] = useState("");
  // a failed save or read is written as an error, a done one in grey
  const [isNoteError, setIsNoteError] = useState(false);
  const [versions, setVersions] = useState<MarkdownVersion[] | null>(null);
  // Counted up whenever the whole document is replaced (an undo, a version, the lock closed on unsaved changes). The
  // editor changes blocks outside React, so a text React already drew has to be drawn again from scratch.
  const [drawnAgain, setDrawnAgain] = useState(0);
  // how much of the document's text is drawn: a part at a time for a large one, the whole while editing
  const [shownChars, setShownChars] = useState(PART_CHARS);
  useEffect(() => setShownChars(PART_CHARS), [runBase, path]);
  const [figure, setFigure] = useState<{ content: FigureContent; caption: string } | null>(null);
  const article = useRef<HTMLDivElement>(null);
  const wrap = useRef<HTMLDivElement>(null);
  const openBlock = useRef<HTMLElement | null>(null);
  const undone = useRef<string[]>([]);
  const frontMatterRaw = useRef("");
  const frontMatterArea = useRef<HTMLTextAreaElement>(null);
  const documentName = path.split("/").pop()?.replace(/\.md$/, "") ?? "document";

  useEffect(() => {
    setText(null);
    setError("");
    setIsUnlocked(false);
    setIsDirty(false);
    undone.current = [];
    fetch(buildFileUrl(runBase, path))
      .then((response) => (response.ok ? response.text() : Promise.reject(new Error(`${response.status}`))))
      .then(setText)
      .catch((reason) => setError(`could not read ${path}: ${reason.message}`));
  }, [runBase, path]);

  const isEditing = useRef(false);
  isEditing.current = isUnlocked || isDirty;
  useEffect(() => {
    if (!liveMs) return undefined;
    const timer = window.setInterval(() => {
      if (isEditing.current) return;
      fetch(buildFileUrl(runBase, path))
        .then((response) => (response.ok ? response.text() : Promise.reject(new Error(`${response.status}`))))
        .then((fresh) => { if (!isEditing.current) setText((shown) => (shown === fresh ? shown : fresh)); })
        .catch(() => undefined);
    }, liveMs);
    return () => window.clearInterval(timer);
  }, [runBase, path, liveMs]);

  const { fields, html, contents, htmlWithoutContents, drawnChars, bodyChars } = useMemo(() => {
    if (text === null) return { fields: [], html: "", contents: "", htmlWithoutContents: "", drawnChars: 0, bodyChars: 0 };
    const split = splitFrontMatter(text);
    frontMatterRaw.current = split.raw;
    // the editor saves the file from the drawn blocks, so it always draws the whole document
    const drawn = isUnlocked ? split.body : cutAtBlockEnd(split.body, shownChars);
    const rendered = DOMPurify.sanitize(renderBlocks(drawn));
    const prepared = prepareHtml(rendered, runBase, path, linkFolder);
    const parts = splitContents(prepared);
    return { fields: split.fields, html: prepared, contents: parts.contents, htmlWithoutContents: parts.rest,
      drawnChars: drawn.length, bodyChars: split.body.length };
  }, [text, runBase, path, linkFolder, shownChars, isUnlocked]);
  const isPartShown = drawnChars < bodyChars;
  // While the lock is open the whole document is shown: a contents list pinned beside the text is not in the text a
  // save would write back.
  const isContentsPinned = isReading && contents !== "" && !isUnlocked;

  useEffect(() => {
    const container = article.current?.querySelector<HTMLElement>(".markdown");
    if (container && html) enhanceDocument(container);
  }, [html, isSource, isContentsPinned, drawnAgain]);

  // Put `source` in the page as the whole document, drawn from scratch.
  const showDocument = useCallback((source: string) => {
    setText(source);
    setDrawnAgain((count) => count + 1);
  }, []);

  // The document as it stands in the page: the front matter, then every block's own markdown. The front matter is no
  // block of the text, so while the lock is open it is one textarea of its own rather than the table a reader sees.
  const readDocument = useCallback((): string => {
    const matter = frontMatterArea.current ? frontMatterArea.current.value : frontMatterRaw.current;
    const container = article.current?.querySelector<HTMLElement>(".markdown");
    const blocks = [...(container?.querySelectorAll<HTMLElement>(":scope > [data-line]") ?? [])];
    const source = blocks.map((block) => {
      const area = block.querySelector<HTMLTextAreaElement>("textarea.block-source");
      return area ? area.value : decodeURIComponent(block.dataset.src ?? "");
    }).join("");
    return matter + source;
  }, []);

  const pushUndone = useCallback((source: string) => {
    const kept = undone.current;
    if (kept[kept.length - 1] === source) return;
    kept.push(source);
    if (kept.length > UNDO_KEPT) kept.shift();
  }, []);

  // Leaving a block puts its markdown back into the block and draws it again, so an edited fence redraws at once.
  const closeBlock = useCallback(async () => {
    const block = openBlock.current;
    openBlock.current = null;
    const area = block?.querySelector<HTMLTextAreaElement>("textarea.block-source");
    if (!block || !area) return;
    const source = area.value;
    block.dataset.src = encodeURIComponent(source);
    block.innerHTML = prepareHtml(DOMPurify.sanitize(renderOneBlock(source)), runBase, path, linkFolder);
    await enhanceDocument(block);
    setIsDirty(true);
    pushUndone(readDocument());
  }, [runBase, path, linkFolder, pushUndone, readDocument]);

  // A textarea per open block, never an editor over the whole note: the browser's own undo, selection and input
  // method keep working, and no keystroke makes HTML that has to be turned back into markdown.
  const showBlockSource = useCallback(async (block: HTMLElement) => {
    if (openBlock.current === block) return;
    await closeBlock();
    const area = document.createElement("textarea");
    area.className = "block-source";
    area.spellcheck = false;
    area.value = decodeURIComponent(block.dataset.src ?? "");
    block.innerHTML = "";
    block.append(area);
    const grow = () => {
      area.style.height = "auto";
      area.style.height = `${Math.max(area.scrollHeight, 28)}px`;
    };
    grow();
    area.oninput = grow;
    area.onblur = () => void closeBlock();
    area.focus();
    openBlock.current = block;
  }, [closeBlock]);

  useEffect(() => {
    if (!isUnlocked) return;
    pushUndone(readDocument());
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && openBlock.current) {
        void closeBlock();
        return;
      }
      if (!(event.metaKey || event.ctrlKey) || event.key !== "z" || event.shiftKey) return;
      if (isSource || openBlock.current || undone.current.length < 2) return;
      event.preventDefault();
      undone.current.pop();
      showDocument(undone.current[undone.current.length - 1]);
      setIsDirty(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isUnlocked, isSource, closeBlock, pushUndone, readDocument, showDocument]);

  // Locking puts the file on disk back in the page, so changes the editor was left with are never half shown. It says
  // whether it locked: an answer of no to the question about unsaved changes keeps the editor open.
  const lockEditor = useCallback(async (): Promise<boolean> => {
    await closeBlock();
    if (isDirty && !window.confirm("Leave the editor and lose the changes you did not save?")) return false;
    setIsUnlocked(false);
    setIsSource(false);
    if (isDirty) showDocument(await (await fetch(buildFileUrl(runBase, path))).text());
    setIsDirty(false);
    return true;
  }, [closeBlock, isDirty, runBase, path, showDocument]);

  const toggleLock = async () => {
    if (isUnlocked) {
      await lockEditor();
      return;
    }
    undone.current = [];
    setIsUnlocked(true);
  };

  // The editor belongs to reading mode: leaving it locks first, so a document is never left editable in the tab.
  const leaveReading = useCallback(async () => {
    if (isUnlocked && !await lockEditor()) return;
    setIsReading(false);
  }, [isUnlocked, lockEditor]);

  useEffect(() => {
    if (!isReading) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !openBlock.current) void leaveReading();
      // while it covers the page, the keyboard stays in the reading mode
      if (!figure) focusTrap.keepTabInside(event, wrap.current);
    };
    document.body.classList.add("no-scroll");
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.classList.remove("no-scroll");
      window.removeEventListener("keydown", onKey);
    };
  }, [isReading, leaveReading, figure]);

  const save = async () => {
    await closeBlock();
    const markdown = readDocument();
    try {
      const saved = await saveMarkdown(runBase, path, markdown);
      setText(markdown);
      setIsDirty(false);
      undone.current = [markdown];
      setVersions(null);
      setNote(`saved · the version it replaced is kept as ${formatStamp(saved.kept_as)}`);
      setIsNoteError(false);
    } catch (reason) {
      setNote(`not saved: ${describeError(reason)}`);
      setIsNoteError(true);
    }
  };

  const showVersions = async () => {
    try {
      setVersions(await fetchJson<MarkdownVersion[]>(`${runBase}/versions/${path.split("/").map(encodeURIComponent).join("/")}`));
    } catch (reason) {
      setNote(`the versions could not be read: ${describeError(reason)}`);
      setIsNoteError(true);
    }
  };

  // Loading a version only puts its text in the editor; Save is what puts it back, and that save keeps the version it
  // replaces like any other.
  const loadVersion = async (at: string) => {
    try {
      const got = await fetchJson<{ at: string; markdown: string }>(
        `${runBase}/version/${path.split("/").map(encodeURIComponent).join("/")}?at=${encodeURIComponent(at)}`);
      showDocument(got.markdown);
      undone.current = [got.markdown];
      setIsDirty(true);
      setVersions(null);
      setNote(`the version of ${formatStamp(at)} is in the editor; Save puts it back`);
      setIsNoteError(false);
    } catch (reason) {
      setNote(`that version could not be read: ${describeError(reason)}`);
      setIsNoteError(true);
    }
  };

  const openDiagram = (figureElement: HTMLElement) => {
    const box = figureElement.querySelector<HTMLElement>(".mermaid");
    const svg = box?.querySelector("svg");
    if (!box || !svg) return;
    // The size comes from the viewBox: Mermaid sizes its SVG to 100% of the column, which a copy alone does not have.
    const viewBox = (svg.getAttribute("viewBox") ?? "").split(/[\s,]+/).map(Number);
    const shown = svg.getBoundingClientRect();
    const width = viewBox[2] > 0 ? viewBox[2] : shown.width || 800;
    const height = viewBox[3] > 0 ? viewBox[3] : shown.height || 600;
    const copy = svg.cloneNode(true) as SVGSVGElement;
    copy.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    copy.setAttribute("width", String(width));
    copy.setAttribute("height", String(height));
    copy.style.maxWidth = "none";
    const index = box.dataset.index ?? "1";
    setFigure({
      content: { kind: "svg", markup: new XMLSerializer().serializeToString(copy), width, height,
        source: box.dataset.src ?? "", name: `${documentName}-diagram-${index}` },
      caption: `${path} · diagram ${index}`,
    });
  };

  // A `#` link scrolls this document to its heading, never the page's address. A figure's Expand, a picture, and a
  // code block's Copy are handled here too, once for the whole document.
  const onClick = (event: MouseEvent<HTMLDivElement>) => {
    const element = event.target as HTMLElement;
    const action = element.closest<HTMLElement>("[data-action]");
    if (action?.dataset.action === "expand") {
      openDiagram(action.closest<HTMLElement>(".figure")!);
      return;
    }
    if (action?.dataset.action === "copy") {
      const code = action.parentElement?.querySelector("code")?.textContent ?? "";
      copyText(code).then(() => {
        action.textContent = "Copied";
      }, () => {
        action.textContent = "Copy failed";
      }).finally(() => window.setTimeout(() => (action.textContent = "Copy"), 1500));
      return;
    }
    const image = element.closest("img");
    if (image && !image.closest("a")) {
      setFigure({ content: { kind: "img", src: image.src, name: decodeURIComponent(image.src.split("/").pop() ?? "figure") },
        caption: image.alt || path });
      return;
    }
    const link = element.closest("a");
    const target = link?.getAttribute("href") ?? "";
    if (!target.startsWith("#")) return;
    event.preventDefault();
    scrollToHeading(target);
  };

  // The caret goes into the block the pointer is on, not into a link, a control or a drawn diagram.
  const onMouseDown = (event: MouseEvent<HTMLDivElement>) => {
    if (!isUnlocked || isSource) return;
    const element = event.target as HTMLElement;
    if (element.closest("a, button, .figure, textarea")) return;
    const block = element.closest<HTMLElement>(".markdown > [data-line]");
    if (!block || block === openBlock.current) return;
    event.preventDefault();
    void showBlockSource(block);
  };

  const scrollToHeading = (target: string) => {
    const id = decodeURIComponent(target.slice(1));
    article.current?.querySelector(`[id="${CSS.escape(id)}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  // A click on the pinned contents scrolls the text beside it to that heading.
  const onContentsClick = (event: MouseEvent<HTMLElement>) => {
    const target = (event.target as HTMLElement).closest("a")?.getAttribute("href") ?? "";
    if (!target.startsWith("#")) return;
    event.preventDefault();
    setIsContentsOpen(false);
    scrollToHeading(target);
  };

  if (error) return <p className="error">{error}</p>;
  if (text === null) return <Spinner />;

  const downloadUrl = buildDownloadUrl(runBase, path);

  return (
    <div ref={wrap} className={`markdown-wrap${isReading ? " reading" : ""}`}
      {...(isReading ? { role: "dialog", "aria-modal": true, "aria-label": path } : {})}>
      <div className="markdown-tools">
        <small className="muted">{path} · {(text.length / 1000).toFixed(1)}k characters{isDirty ? " · not saved" : ""}</small>
        <button className={`btn ghost small${isSource ? " active" : ""}`} onClick={() => { void closeBlock(); setIsSource(!isSource); }}>
          {isSource ? "Rendered" : "Source"}
        </button>
        {!isUnlocked && <a className="btn ghost small" href={buildFileUrl(runBase, path)} target="_blank" rel="noreferrer">Open</a>}
        {!isUnlocked && (
          <a className="btn ghost small" href={downloadUrl} title="a document with figures comes as a zip">
            <Icon name="download" size={14} /> Download
          </a>
        )}
        {isReading && (
          <button className={`btn ghost small${isUnlocked ? " active" : ""}`} onClick={() => void toggleLock()}
            title={isUnlocked ? "lock editing again" : "edit this document in the page"}>
            <Icon name={isUnlocked ? "unlock" : "lock"} size={14} /> {isUnlocked ? "Editing" : "Edit"}
          </button>
        )}
        {isUnlocked && <button className="btn ghost small" onClick={() => void showVersions()}>
          <Icon name="reset" size={14} /> Versions
        </button>}
        {isUnlocked && <button className="btn small" disabled={!isDirty} onClick={() => void save()}>
          <Icon name="check" size={14} /> Save
        </button>}
        <button className={`btn ghost small${isReading ? " active" : ""}`}
          onClick={() => (isReading ? void leaveReading() : setIsReading(true))}
          title={isReading ? "leave full screen (Esc)" : "open this document full screen"}>
          <Icon name={isReading ? "x" : "expand"} size={14} /> {isReading ? "Close" : "Full screen"}
        </button>
      </div>
      {note && <p className={`markdown-note ${isNoteError ? "error" : "muted"}`} role="status">{note}</p>}
      {isSource ? (
        isUnlocked ? (
          <textarea className="source" value={text} spellCheck={false}
            onChange={(event) => { setText(event.target.value); setIsDirty(true); }} />
        ) : (
          <pre className="source">{text}</pre>
        )
      ) : (
        <div className="markdown-body">
        {isContentsPinned && (
          <nav className={`markdown-contents${isContentsOpen ? " open" : ""}`} aria-label="contents" onClick={onContentsClick}>
            <strong>Contents</strong>
            <button type="button" className="markdown-contents-toggle" aria-expanded={isContentsOpen}
              onClick={() => setIsContentsOpen(!isContentsOpen)}>
              Contents <Icon name="chevron" size={14} />
            </button>
            <div dangerouslySetInnerHTML={{ __html: contents }} />
          </nav>
        )}
        <div className="markdown-scroll" ref={article} onClick={onClick} onMouseDown={onMouseDown}
          onKeyDown={(event) => {
            const target = event.target as HTMLElement;
            if ((event.key === "Enter" || event.key === " ") && target.matches("img[role='button']")) {
              event.preventDefault();
              target.click();
            }
          }}>
          <div className="markdown-page">
            {isUnlocked && frontMatterRaw.current !== "" && (
              <textarea key={`front-matter-${drawnAgain}`} ref={frontMatterArea} className="front-matter-source" spellCheck={false}
                aria-label="front matter" defaultValue={frontMatterRaw.current}
                onChange={() => setIsDirty(true)} />
            )}
            {!isUnlocked && fields.length > 0 && (
              <table className="front-matter">
                <tbody>
                  {fields.map(([key, value]) => (
                    <tr key={key}><th>{key}</th><td>{value || "—"}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
            <article key={`text-${drawnAgain}`} className={`markdown${isUnlocked ? " editing" : ""}`}
              dangerouslySetInnerHTML={{ __html: isContentsPinned ? htmlWithoutContents : html }} />
            {isPartShown && (
              <div className="markdown-more" role="status">
                <span className="muted">Shown: {Math.round((100 * drawnChars) / bodyChars)}% of the document
                  ({(drawnChars / 1000).toFixed(0)}k of {(bodyChars / 1000).toFixed(0)}k characters).</span>
                <button className="btn" onClick={() => setShownChars(drawnChars + PART_CHARS)}>Show more</button>
              </div>
            )}
          </div>
        </div>
        </div>
      )}
      {versions && (
        <Dialog title={`Versions of ${documentName}`} onClose={() => setVersions(null)}>
          {versions.length === 0 ? (
            <p className="muted">This document has not been saved from the page yet, so no version was kept.</p>
          ) : (
            <ul className="version-list">
              {versions.map((version) => (
                <li key={version.at}>
                  <button className="btn ghost small" onClick={() => void loadVersion(version.at)}>
                    {formatStamp(version.at)} · {(version.size_bytes / 1000).toFixed(1)}k
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Dialog>
      )}
      {figure && <FigureViewer content={figure.content} caption={figure.caption} onClose={() => setFigure(null)} />}
    </div>
  );
}
