import DOMPurify from "dompurify";
import { diffLines, diffWordsWithSpace } from "diff";
import { marked } from "marked";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import { describeError } from "../api";
import { copyText } from "../clipboard";
import {
  fetchInstructionCatalogue, fetchInstructionFile, fetchInstructionPreview, fetchInstructionVersion,
  fetchInstructionVersions, resetInstruction, saveInstruction,
} from "../instructionsApi";
import type {
  InstructionCatalogue, InstructionFile, InstructionFileEntry, InstructionFileKind, InstructionStep, InstructionVersion,
} from "../instructionsApi";
import { enhanceDocument, formatStamp } from "../markdownEnhance";
import { usePersistentState } from "../viewState";
import Icon from "./Icon";
import Spinner from "./Spinner";

// The workers' instructions: every workflow's phases and steps on the left, one file on the
// right to read, edit, compare with its default or any version, see its history and the full prompt its step's worker
// gets. The defaults ship with the program and are never written; an edit and every version are kept in the workspace.

type Tab = "read" | "edit" | "compare" | "history" | "prompt";
const TABS: { key: Tab; label: string }[] = [
  { key: "read", label: "Read" }, { key: "edit", label: "Edit" }, { key: "compare", label: "Compare" },
  { key: "history", label: "History" }, { key: "prompt", label: "Full prompt" },
];
// What each kind of file is, in the words the tree shows beside it
const KIND_LABELS: Record<InstructionFileKind, string> = {
  instruction: "instruction", shared: "shared guideline", document: "added by a rule", compaction: "compaction",
};
// Where the program puts what each `{{INJECT:x}}` marker stands for
const INJECT_LABELS: Record<string, string> = {
  base: "the rules of the recording's profile or medium go here",
  contracts: "the rules the answer is checked against go here",
  templates: "the shape of the answer goes here",
};
// An unchanged run of lines longer than this is folded in a comparison, its first and last lines kept
const FOLD_LINES = 8;
const KEPT_AROUND_FOLD = 3;

interface OpenFile {
  id: string;
  workflow: string;
  step: string;
}

const isOpenFile = (value: unknown) => typeof value === "object" && value !== null && "id" in value;

const escapeHtml = (text: string) => text.replace(/[&<>"]/g, (letter) => `&#${letter.charCodeAt(0)};`);

// The markers the program fills, drawn as chips a reader can tell from the text: a shared guideline opens on a click
function markMarkers(html: string, kind: InstructionFileKind): string {
  const marked = html
    .replace(/\{\{SHARED:([a-z0-9-]+)\}\}/g, (_whole, name: string) =>
      `<button type="button" class="instruction-marker shared" data-shared="${escapeHtml(name)}">`
      + `Shared guideline: ${escapeHtml(name.replace(/-/g, " "))} ↗</button>`)
    .replace(/\{\{INJECT:([a-z]+)\}\}/g, (_whole, name: string) =>
      `<span class="instruction-marker">${escapeHtml(INJECT_LABELS[name] ?? `the ${name} documents go here`)}</span>`)
    .replace(/\{\{([A-Z_]+)\}\}/g, (_whole, name: string) =>
      `<span class="instruction-marker" title="filled in by the program">${escapeHtml(name)}</span>`);
  if (kind !== "compaction") return marked;
  return marked.replace(/\{([a-z_]+)\}/g, (_whole, name: string) =>
    `<span class="instruction-marker" title="filled in by the program">${escapeHtml(name.replace(/_/g, " "))}</span>`);
}

function RenderedInstruction({ text, kind, onOpenShared }: {
  text: string; kind: InstructionFileKind; onOpenShared: (id: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const html = useMemo(() => DOMPurify.sanitize(markMarkers(marked.parse(text, { async: false }) as string, kind)),
    [text, kind]);
  useEffect(() => {
    if (box.current) void enhanceDocument(box.current);
  }, [html]);
  const onClick = (event: MouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    const shared = target.closest<HTMLElement>("[data-shared]");
    if (shared?.dataset.shared) onOpenShared(`instructions/_shared/${shared.dataset.shared}.md`);
    const copy = target.closest<HTMLElement>("[data-action='copy']");
    const code = copy?.parentElement?.querySelector("code");
    if (code) void copyText(code.textContent ?? "").catch(() => undefined);
  };
  if (!text.trim()) return <p className="muted">This text is empty.</p>;
  return <div ref={box} className="markdown instruction-markdown" onClick={onClick} dangerouslySetInnerHTML={{ __html: html }} />;
}

type DiffBlock = { kind: "same"; lines: string[] } | { kind: "change"; removed: string; added: string };

// The two texts as blocks: runs of lines both hold, and each place where one holds lines the other does not
function buildDiffBlocks(left: string, right: string): DiffBlock[] {
  const blocks: DiffBlock[] = [];
  for (const part of diffLines(left, right)) {
    const last = blocks[blocks.length - 1];
    if (!part.added && !part.removed) {
      blocks.push({ kind: "same", lines: part.value.replace(/\n$/, "").split("\n") });
    } else if (last?.kind === "change" && part.added && !last.added) {
      last.added = part.value;
    } else {
      blocks.push({ kind: "change", removed: part.removed ? part.value : "", added: part.added ? part.value : "" });
    }
  }
  return blocks;
}

// One side of a changed block, the words the other side lacks marked
function renderChangedSide(removed: string, added: string, side: "removed" | "added"): ReactNode {
  const text = side === "removed" ? removed : added;
  if (!text) return <span className="diff-empty">nothing</span>;
  if (!removed || !added) return text.replace(/\n$/, "");
  return diffWordsWithSpace(removed, added)
    .filter((word) => (side === "removed" ? !word.added : !word.removed))
    .map((word, index) => (word.added || word.removed
      ? <mark key={index} className={`diff-word ${side}`}>{word.value}</mark>
      : <span key={index}>{word.value}</span>));
}

function SameBlock({ lines }: { lines: string[] }) {
  const [isOpen, setIsOpen] = useState(false);
  if (lines.length <= FOLD_LINES || isOpen) return <div className="diff-row same"><pre>{lines.join("\n")}</pre><pre>{lines.join("\n")}</pre></div>;
  const head = lines.slice(0, KEPT_AROUND_FOLD).join("\n");
  const tail = lines.slice(-KEPT_AROUND_FOLD).join("\n");
  const hidden = lines.length - 2 * KEPT_AROUND_FOLD;
  return (
    <>
      <div className="diff-row same"><pre>{head}</pre><pre>{head}</pre></div>
      <button type="button" className="btn ghost small diff-fold" onClick={() => setIsOpen(true)}>
        Show {hidden} unchanged lines
      </button>
      <div className="diff-row same"><pre>{tail}</pre><pre>{tail}</pre></div>
    </>
  );
}

function DiffView({ left, right, leftLabel, rightLabel }: { left: string; right: string; leftLabel: string; rightLabel: string }) {
  const blocks = useMemo(() => buildDiffBlocks(left, right), [left, right]);
  const changes = blocks.filter((block) => block.kind === "change").length;
  if (left === right) return <p className="muted">The two texts are the same.</p>;
  return (
    <div className="diff">
      <p className="muted">{changes === 1 ? "One place differs." : `${changes} places differ.`} Lines only on the left are
        marked red, lines only on the right green.</p>
      <div className="diff-row diff-head"><strong>{leftLabel}</strong><strong>{rightLabel}</strong></div>
      {blocks.map((block, index) => block.kind === "same" ? <SameBlock key={index} lines={block.lines} /> : (
        <div key={index} className="diff-row change">
          <pre className="removed">{renderChangedSide(block.removed, block.added, "removed")}</pre>
          <pre className="added">{renderChangedSide(block.removed, block.added, "added")}</pre>
        </div>
      ))}
    </div>
  );
}

function StateBadge({ file, hasDraft }: { file: Pick<InstructionFileEntry, "is_edited" | "is_default_changed">; hasDraft: boolean }) {
  return (
    <span className="instruction-badges">
      {hasDraft && <span className="pill new">not saved</span>}
      {file.is_default_changed && <span className="pill warn" title="the program's default changed since this edit was made">default changed</span>}
      {file.is_edited ? <span className="pill edited">edited</span> : <span className="pill">default</span>}
    </span>
  );
}

function StepFiles({ step, workflow, files, drafts, openFile, onOpen }: {
  step: InstructionStep; workflow: string; files: Map<string, InstructionFileEntry>; drafts: Record<string, string>;
  openFile: OpenFile | null; onOpen: (open: OpenFile) => void;
}) {
  const edited = step.files.filter((id) => files.get(id)?.is_edited).length;
  return (
    <details className="instruction-step" open={step.files.includes(openFile?.id ?? "")}>
      <summary>
        <span>{step.title}</span>
        {edited > 0 && <span className="pill edited">{edited === 1 ? "1 edited" : `${edited} edited`}</span>}
      </summary>
      <ul>
        {step.files.map((id) => {
          const entry = files.get(id);
          const isOpen = openFile?.id === id && openFile.step === step.key;
          return (
            <li key={id}>
              <button type="button" className={`instruction-file-button${isOpen ? " open" : ""}`} aria-current={isOpen || undefined}
                aria-label={`${entry?.title ?? id} (${KIND_LABELS[entry?.kind ?? "instruction"]}), `
                  + `${id in drafts ? "not saved, " : ""}${entry?.is_edited ? "edited" : "default"}`}
                onClick={() => onOpen({ id, workflow, step: step.key })}>
                <span className="instruction-file-title">{entry?.title ?? id}</span>
                <small className="muted">{KIND_LABELS[entry?.kind ?? "instruction"]}</small>
                {entry && <StateBadge file={entry} hasDraft={id in drafts} />}
              </button>
            </li>
          );
        })}
      </ul>
    </details>
  );
}

function VersionPicker({ value, onChange, options, label }: {
  value: string; onChange: (value: string) => void; options: { key: string; label: string }[]; label: string;
}) {
  return (
    <label className="instruction-compare-pick">
      <span className="muted">{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => <option key={option.key} value={option.key}>{option.label}</option>)}
      </select>
    </label>
  );
}

export default function InstructionsView({ onMessage }: { onMessage: (text: string, isError?: boolean) => void }) {
  const [catalogue, setCatalogue] = useState<InstructionCatalogue | null>(null);
  const [workflowKey, setWorkflowKey] = usePersistentState("instructionsWorkflow", "video");
  const [variant, setVariant] = usePersistentState("instructionsVariant", "");
  const [search, setSearch] = useState("");
  const [openFile, setOpenFile] = usePersistentState<OpenFile | null>("instructionsFile", null, isOpenFile);
  const [file, setFile] = useState<InstructionFile | null>(null);
  const [tab, setTab] = usePersistentState<Tab>("instructionsTab", "read");
  // Unsaved texts by file, so moving to another file keeps what was typed
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [restoredFrom, setRestoredFrom] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [problems, setProblems] = useState<string[]>([]);
  const [isSaving, setIsSaving] = useState(false);
  const [versions, setVersions] = useState<InstructionVersion[]>([]);
  const [versionTexts, setVersionTexts] = useState<Record<string, string>>({});
  const [compareLeft, setCompareLeft] = useState("default");
  const [compareRight, setCompareRight] = useState("active");
  const [preview, setPreview] = useState<{ text: string; error: string } | null>(null);
  const [isPreviewRaw, setIsPreviewRaw] = useState(false);

  const reloadCatalogue = useCallback(() => {
    fetchInstructionCatalogue().then(setCatalogue).catch((error) => onMessage(describeError(error), true));
  }, [onMessage]);
  useEffect(reloadCatalogue, [reloadCatalogue]);

  const fileId = openFile?.id ?? "";
  const reloadFile = useCallback(() => {
    if (!fileId) return;
    fetchInstructionFile(fileId).then(setFile).catch((error) => {
      setFile(null);
      onMessage(describeError(error), true);
    });
    fetchInstructionVersions(fileId).then(setVersions).catch(() => setVersions([]));
  }, [fileId, onMessage]);
  useEffect(() => {
    setFile(null);
    setProblems([]);
    setNote("");
    setVersionTexts({});
    setCompareLeft("default");
    setCompareRight("active");
    setPreview(null);
    reloadFile();
  }, [reloadFile]);

  const files = useMemo(() => new Map((catalogue?.files ?? []).map((entry) => [entry.id, entry])), [catalogue]);
  const workflow = catalogue?.workflows.find((entry) => entry.key === workflowKey) ?? catalogue?.workflows[0];
  const draft = file && fileId in drafts ? drafts[fileId] : file?.text ?? "";
  const hasDraft = Boolean(file) && draft !== file?.text;
  const steps = workflow?.phases.flatMap((phase) => phase.steps) ?? [];
  const openStep = steps.find((step) => step.key === openFile?.step);
  const words = search.trim().toLowerCase();
  const isShown = (step: InstructionStep) => (!variant || step.variants.includes(variant))
    && (!words || step.title.toLowerCase().includes(words)
      || step.files.some((id) => `${files.get(id)?.title ?? ""} ${id}`.toLowerCase().includes(words)));

  const setDraft = (text: string) => setDrafts((all) => ({ ...all, [fileId]: text }));
  const dropDraft = () => {
    setDrafts((all) => Object.fromEntries(Object.entries(all).filter(([id]) => id !== fileId)));
    setRestoredFrom((all) => Object.fromEntries(Object.entries(all).filter(([id]) => id !== fileId)));
  };

  const save = async () => {
    if (!file) return;
    setIsSaving(true);
    try {
      const restored = restoredFrom[fileId];
      const saved = await saveInstruction(fileId, draft, note || (restored ? `restored ${formatStamp(restored)}` : ""),
        restored ? "restore" : "edit");
      setProblems(saved.problems);
      if (saved.is_saved) {
        dropDraft();
        setNote("");
        onMessage(saved.is_changed === false ? `${file.title}: nothing changed.`
          : `${file.title} saved. The next call of a running run reads it.`);
        reloadFile();
        reloadCatalogue();
      }
    } catch (error) {
      onMessage(describeError(error), true);
    } finally {
      setIsSaving(false);
    }
  };

  const reset = async () => {
    if (!file) return;
    try {
      await resetInstruction(fileId);
      dropDraft();
      setProblems([]);
      onMessage(`${file.title}: the default is in force again; your edit stays in its history.`);
      reloadFile();
      reloadCatalogue();
    } catch (error) {
      onMessage(describeError(error), true);
    }
  };

  // The text a comparison side names: the default, the text in force, the unsaved draft, the default the edit was made
  // over, or a version, fetched once
  const readSide = (key: string): string | undefined => {
    if (!file) return undefined;
    if (key === "default") return file.default;
    if (key === "active") return file.text;
    if (key === "draft") return draft;
    if (key === "default-at-edit") return file.default_at_edit;
    return versionTexts[key];
  };
  useEffect(() => {
    for (const key of [compareLeft, compareRight]) {
      if (!key.startsWith("v:") || key in versionTexts || !fileId) continue;
      fetchInstructionVersion(fileId, key.slice(2))
        .then((version) => setVersionTexts((texts) => ({ ...texts, [key]: version.text })))
        .catch((error) => onMessage(describeError(error), true));
    }
  }, [compareLeft, compareRight, versionTexts, fileId, onMessage]);
  const sideOptions = [
    { key: "default", label: "The default the program ships" },
    { key: "active", label: "The text in force" },
    ...(hasDraft ? [{ key: "draft", label: "Your draft, not saved" }] : []),
    ...(file?.is_default_changed ? [{ key: "default-at-edit", label: "The default your edit was made over" }] : []),
    ...versions.map((version) => ({
      key: `v:${version.at}`,
      label: `${formatStamp(version.at)} · ${version.kind}${version.note ? ` · ${version.note}` : ""}`,
    })),
  ];
  const sideLabel = (key: string) => sideOptions.find((option) => option.key === key)?.label ?? key;

  // The shared guidelines the draft includes, each once: their text can be put in place of the include
  const includedGuidelines = [...new Set([...draft.matchAll(/\{\{SHARED:([a-z0-9-]+)\}\}/g)].map((match) => match[1]))];
  const inlineGuideline = async (name: string) => {
    try {
      const guideline = await fetchInstructionFile(`instructions/_shared/${name}.md`);
      setDraft(draft.split(`{{SHARED:${name}}}`).join(guideline.text.trim()));
      onMessage(`The text of ${name.replace(/-/g, " ")} is in this file now: edit it, then Save.`);
    } catch (error) {
      onMessage(describeError(error), true);
    }
  };

  const restore = async (version: InstructionVersion) => {
    try {
      const kept = await fetchInstructionVersion(fileId, version.at);
      setDraft(kept.text);
      setRestoredFrom((all) => ({ ...all, [fileId]: version.at }));
      setTab("edit");
      onMessage(`The version of ${formatStamp(version.at)} is in the editor: Save puts it in force.`);
    } catch (error) {
      onMessage(describeError(error), true);
    }
  };

  const previewUse = openStep ? { step: openStep.key, workflow: openFile?.workflow ?? "" } : file?.used_by[0];
  const previewStep = previewUse?.step ?? "";
  const previewWorkflow = previewUse?.workflow ?? "";
  const canPreview = Boolean(file) && file?.kind !== "compaction" && Boolean(previewStep);
  useEffect(() => {
    if (tab !== "prompt" || !canPreview || !fileId) return;
    let isCurrent = true;
    setPreview(null);
    fetchInstructionPreview(previewStep, previewWorkflow, hasDraft ? fileId : "", hasDraft ? draft : "")
      .then((answer) => { if (isCurrent) setPreview(answer); })
      .catch((error) => { if (isCurrent) setPreview({ text: "", error: describeError(error) }); });
    return () => { isCurrent = false; };
  }, [tab, canPreview, previewStep, previewWorkflow, fileId, hasDraft, draft]);

  const openShared = (id: string) => setOpenFile({ id, workflow: workflow?.key ?? "", step: openFile?.step ?? "" });
  const shownTabs = TABS.filter((entry) => entry.key !== "prompt" || canPreview);
  const usedBy = (file?.used_by ?? []).map((use) => {
    const owner = catalogue?.workflows.find((entry) => entry.key === use.workflow);
    const step = owner?.phases.flatMap((phase) => phase.steps).find((entry) => entry.key === use.step);
    return `${owner?.title ?? use.workflow} · ${step?.title ?? use.step}`;
  });

  return (
    <div className={`detail instructions${openFile ? " has-file" : ""}`}>
      <header className="detail-head">
        <div className="detail-title">
          <h2>Instructions</h2>
          <small className="muted">What each worker is told, by workflow, phase and step. The program's defaults are
            never changed: your edits and every version are kept beside the runs, and Reset brings a default back.</small>
        </div>
      </header>
      {!catalogue ? <Spinner /> : (
        <div className="instructions-columns">
          <nav className="instructions-tree" aria-label="instructions by workflow">
            <div className="instruction-workflows" role="tablist" aria-label="workflow">
              {catalogue.workflows.map((entry) => (
                <button key={entry.key} type="button" role="tab" aria-selected={entry.key === workflow?.key}
                  className={`btn${entry.key === workflow?.key ? " primary" : ""}`}
                  onClick={() => { setWorkflowKey(entry.key); setVariant(""); }}>
                  {entry.title}
                </button>
              ))}
            </div>
            {workflow && (
              <label className="instruction-variant">
                <span className="muted">Shown for</span>
                <select value={variant} onChange={(event) => setVariant(event.target.value)}>
                  <option value="">every profile and mode</option>
                  {workflow.variants.map((entry) => <option key={entry.key} value={entry.key}>{entry.title}</option>)}
                </select>
              </label>
            )}
            <label className="instruction-search">
              <Icon name="search" size={14} />
              <input type="search" placeholder="Find a step or a file" value={search}
                onChange={(event) => setSearch(event.target.value)} aria-label="find a step or a file" />
            </label>
            {workflow?.phases.map((phase) => {
              const shown = phase.steps.filter(isShown);
              if (shown.length === 0) return null;
              return (
                <section key={phase.number} className="instruction-phase">
                  <h3>Phase {phase.number} · {(variant && phase.variant_titles?.[variant]) || phase.title}</h3>
                  {shown.map((step) => (
                    <StepFiles key={step.key} step={step} workflow={workflow.key} files={files} drafts={drafts}
                      openFile={openFile} onOpen={(open) => { setOpenFile(open); }} />
                  ))}
                </section>
              );
            })}
          </nav>
          <section className="instructions-file" aria-label="the open instruction">
            {!openFile && <p className="muted">Pick a step's file on the left to read or edit it.</p>}
            {openFile && (
              <button type="button" className="btn ghost instructions-back" onClick={() => setOpenFile(null)}>
                <Icon name="back" size={14} /> Every step
              </button>
            )}
            {openFile && !file && <Spinner />}
            {file && (
              <>
                <div className="instruction-file-head">
                  <h3>{file.title}</h3>
                  <StateBadge file={file} hasDraft={hasDraft} />
                </div>
                <p className="muted instruction-file-id"><code>{file.id}</code></p>
                {openStep?.who && <p className="instruction-who">{openStep.who}</p>}
                {usedBy.length > 0 && (
                  <p className="instruction-used-by">
                    <span className="muted">Sent by</span>
                    {usedBy.map((label) => <span key={label} className="pill">{label}</span>)}
                  </p>
                )}
                {file.is_default_changed && (
                  <p className="instruction-warning">
                    The program's default changed since this edit was made. Compare shows what changed in the default.
                  </p>
                )}
                <div className="instruction-tabs" role="tablist" aria-label="what to do with the file">
                  {shownTabs.map((entry) => (
                    <button key={entry.key} type="button" role="tab" aria-selected={tab === entry.key}
                      className={tab === entry.key ? "on" : ""}
                      onClick={() => {
                        setTab(entry.key);
                        if (entry.key === "compare" && file.is_default_changed) {
                          setCompareLeft("default-at-edit");
                          setCompareRight("default");
                        }
                      }}>
                      {entry.label}{entry.key === "history" && versions.length > 0 ? ` (${versions.length})` : ""}
                    </button>
                  ))}
                </div>

                {tab === "read" && <RenderedInstruction text={file.text} kind={file.kind} onOpenShared={openShared} />}

                {tab === "edit" && (
                  <div className="instruction-edit">
                    <div className="instruction-edit-columns">
                      <label className="instruction-source">
                        <span className="muted">Markdown</span>
                        <textarea value={draft} spellCheck={false} onChange={(event) => setDraft(event.target.value)}
                          aria-label={`the text of ${file.title}`} />
                      </label>
                      <div className="instruction-live">
                        <span className="muted">How it reads</span>
                        <RenderedInstruction text={draft} kind={file.kind} onOpenShared={openShared} />
                      </div>
                    </div>
                    {includedGuidelines.length > 0 && (
                      <div className="instruction-includes">
                        <span className="muted">This file includes shared guidelines. To change one for this file
                          alone, put its text here in place of the include:</span>
                        {includedGuidelines.map((name) => (
                          <button key={name} type="button" className="btn ghost small" onClick={() => void inlineGuideline(name)}>
                            Put the text of {name.replace(/-/g, " ")} here
                          </button>
                        ))}
                      </div>
                    )}
                    {restoredFrom[fileId] && (
                      <p className="muted">Restoring the version of {formatStamp(restoredFrom[fileId])}.</p>
                    )}
                    {problems.length > 0 && (
                      <div className="instruction-problems" role="alert">
                        <strong>Not saved: the program could not build a worker's message from this text.</strong>
                        <ul>{problems.map((problem) => <li key={problem}>{problem}</li>)}</ul>
                      </div>
                    )}
                    <div className="instruction-actions">
                      <input type="text" placeholder="Why you changed it (kept with the version)" value={note}
                        onChange={(event) => setNote(event.target.value)} aria-label="why you changed it" />
                      <button type="button" className="btn primary" disabled={!hasDraft || isSaving} onClick={() => void save()}>
                        {isSaving ? <><span className="spinner" aria-hidden="true" /> Saving…</> : "Save"}
                      </button>
                      <button type="button" className="btn ghost" disabled={!hasDraft || isSaving} onClick={dropDraft}>
                        Discard changes
                      </button>
                      <button type="button" className="btn ghost" disabled={!file.is_edited || isSaving} onClick={() => void reset()}
                        title="put the program's default back in force; your edit stays in the history">
                        <Icon name="reset" size={14} /> Reset to default
                      </button>
                    </div>
                    <p className="muted">A running run reads a saved text at its next call.</p>
                  </div>
                )}

                {tab === "compare" && (
                  <div className="instruction-compare">
                    <div className="instruction-compare-picks">
                      <VersionPicker label="Left" value={compareLeft} onChange={setCompareLeft} options={sideOptions} />
                      <VersionPicker label="Right" value={compareRight} onChange={setCompareRight} options={sideOptions} />
                    </div>
                    {readSide(compareLeft) === undefined || readSide(compareRight) === undefined
                      ? <Spinner />
                      : <DiffView left={readSide(compareLeft) ?? ""} right={readSide(compareRight) ?? ""}
                          leftLabel={sideLabel(compareLeft)} rightLabel={sideLabel(compareRight)} />}
                  </div>
                )}

                {tab === "history" && (
                  versions.length === 0 ? <p className="muted">Nobody has changed this file: the default is in force.</p> : (
                    <ol className="instruction-history">
                      {versions.map((version) => (
                        <li key={version.at}>
                          <div>
                            <strong>{formatStamp(version.at)}</strong>
                            <span className="pill">{version.kind === "reset" ? "default put back"
                              : version.kind === "restore" ? "earlier version saved again" : "edited"}</span>
                            {version.account && <span className="muted">by {version.account}</span>}
                          </div>
                          {version.note && <p>{version.note}</p>}
                          <div className="instruction-history-actions">
                            <button type="button" className="btn ghost small" onClick={() => {
                              setCompareLeft(`v:${version.at}`);
                              setCompareRight("active");
                              setTab("compare");
                            }}>Compare with the text in force</button>
                            <button type="button" className="btn ghost small" onClick={() => void restore(version)}>
                              <Icon name="restore" size={14} /> Restore
                            </button>
                          </div>
                        </li>
                      ))}
                    </ol>
                  )
                )}

                {tab === "prompt" && canPreview && (
                  <div className="instruction-prompt">
                    <p className="muted">
                      The whole system prompt the worker of {openStep?.title ?? previewStep} gets
                      {hasDraft ? ", with your draft in place of this file" : ""}, before the run adds its recording's
                      profile and tags.
                      <label className="instruction-raw"><input type="checkbox" checked={isPreviewRaw}
                        onChange={(event) => setIsPreviewRaw(event.target.checked)} /> the exact characters sent</label>
                    </p>
                    {!preview ? <Spinner />
                      : preview.error ? <p className="instruction-problems" role="alert">{preview.error}</p>
                        : isPreviewRaw ? <pre className="worker-text">{preview.text}</pre>
                          : <RenderedInstruction text={preview.text} kind="instruction" onOpenShared={openShared} />}
                  </div>
                )}
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
