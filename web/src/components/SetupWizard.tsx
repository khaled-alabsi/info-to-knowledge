import { useEffect, useRef, useState } from "react";
import { describeError, sendJson } from "../api";
import type { CommandOutput, SetupCheck, SetupModel, SetupRuntime, SetupSystem } from "../api";
import Dialog from "./Dialog";
import Icon from "./Icon";

// The setup wizard: what a run needs (one model host, its models, the tools), each with its state, the steps to set it
// up on macOS, Windows and Linux, and a button that opens the download page or downloads the recommended models.

const DOWNLOAD_POLL_MS = 2000;
const SYSTEM_LABELS: Record<SetupSystem, string> = { mac: "macOS", windows: "Windows", linux: "Linux" };
const STEPS = ["Model host", "Models", "Tools"] as const;
type Step = (typeof STEPS)[number];
// The host the page recommends, offered first when none is installed
const RECOMMENDED_HOST = "lmstudio";
// The spec's provider each host or API is configured with
const PROVIDER_OF: Record<string, string> = {
  lmstudio: "lmstudio", ollama: "ollama", mlx: "openai-compatible", openai: "openai", anthropic: "anthropic",
};

export type RuntimeState = "ready" | "missing" | "not running" | "key missing" | "not configured" | "not installed" | "not needed";

// One word for a runtime's facts. Needed and absent is what blocks a run; installed and used by no setting is
// "not configured", told apart from "not installed", which is nothing to do.
export function describeRuntime(runtime: SetupRuntime): RuntimeState {
  if (runtime.kind === "tool") return !runtime.is_needed ? "not needed" : runtime.is_installed ? "ready" : "missing";
  if (runtime.is_needed) {
    if (!runtime.is_installed) return runtime.kind === "api" ? "key missing" : "missing";
    return runtime.kind === "host" && !runtime.is_running ? "not running" : "ready";
  }
  return runtime.is_installed ? "not configured" : "not installed";
}

export type ModelState = "ready" | "not configured" | "host not answering" | "not downloaded";

export function describeModel(model: SetupModel): ModelState {
  if (!model.is_configured) return "not configured";
  if (model.is_on_host === null) return "host not answering";
  return model.is_on_host ? "ready" : "not downloaded";
}

const BLOCKING_STATES: RuntimeState[] = ["missing", "not running", "key missing"];

// What each step of the wizard lacks for a run started now: the one rule for the steps' marks and the top bar's warning
export function listStepProblems(check: SetupCheck): Record<Step, boolean> {
  const usedHosts = check.runtimes.filter((runtime) => runtime.kind !== "tool" && runtime.is_needed);
  return {
    "Model host": !check.has_models || usedHosts.length === 0 || usedHosts.some((runtime) => BLOCKING_STATES.includes(describeRuntime(runtime))),
    // the scanned-page reader's model is a row of the Models step, so its missing download marks that step too
    Models: check.models.some((model) => !model.is_configured || model.is_on_host === false)
      || check.runtimes.some((runtime) => runtime.key === "unlimited-ocr" && runtime.is_needed && !runtime.is_installed),
    Tools: check.runtimes.some((runtime) => runtime.kind === "tool" && BLOCKING_STATES.includes(describeRuntime(runtime))),
  };
}

// Whether a run started now would lack something: the top bar's warning
export function hasSetupProblem(check: SetupCheck): boolean {
  return Object.values(listStepProblems(check)).some(Boolean);
}

export function detectSystem(userAgent: string): SetupSystem {
  if (/Mac/i.test(userAgent)) return "mac";
  if (/Win/i.test(userAgent)) return "windows";
  return "linux";
}

function stateClass(state: RuntimeState | ModelState): string {
  if (state === "ready") return "complete";
  if (state === "not configured" || state === "host not answering") return "queued";
  if (state === "not installed" || state === "not needed") return "";
  return "failed";
}

function RuntimeCard({ runtime, system, onSystem }: { runtime: SetupRuntime; system: SetupSystem; onSystem: (system: SetupSystem) => void }) {
  const state = describeRuntime(runtime);
  // A tab per system only when the systems' steps differ
  const isSameEverywhere = new Set(Object.values(runtime.steps).map((steps) => steps.join("\n"))).size === 1;
  return (
    <section className="setup-card" aria-label={runtime.name}>
      <div className="setup-card-head">
        <strong>{runtime.name}</strong>
        <span className={`pill ${stateClass(state)}`}>{state}</span>
      </div>
      <p className="muted">{runtime.purpose}</p>
      {state !== "ready" && state !== "not needed" && (
        <>
          {state !== "not running" && !isSameEverywhere && <div className="segmented" role="tablist" aria-label="system">
            {(Object.keys(SYSTEM_LABELS) as SetupSystem[]).map((key) => (
              <button key={key} role="tab" aria-selected={system === key} className={system === key ? "on" : ""}
                onClick={() => onSystem(key)}>{SYSTEM_LABELS[key]}</button>
            ))}
          </div>}
          {/* Installed with its server off: only how to start it. Missing: how to install it, and its page */}
          <ol className="setup-steps">
            {(state === "not running" ? runtime.start_steps : runtime.steps[system]).map((line) => <li key={line}>{line}</li>)}
          </ol>
          {runtime.download_url && state !== "not running" && (
            <div className="dialog-actions setup-actions">
              <a className="btn" href={runtime.download_url} target="_blank" rel="noreferrer">
                <Icon name="download" /> {runtime.kind === "api" ? "Open the key page" : "Open the download page"}
              </a>
            </div>
          )}
        </>
      )}
    </section>
  );
}

// No model configured, or the person wants another host: which host, which model, and the key or address it needs
function HostChooser({ runtimes, onSaved, onMessage }: { runtimes: SetupRuntime[]; onSaved: () => void; onMessage: (text: string, isError?: boolean) => void }) {
  const choices = runtimes.filter((runtime) => runtime.kind !== "tool");
  // A host this machine has is offered first; LM Studio, the recommended one, when none is installed
  const [chosen, setChosenKey] = useState(choices.find((runtime) => runtime.kind === "host" && runtime.is_installed)?.key ?? RECOMMENDED_HOST);
  const [modelId, setModelId] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const runtime = choices.find((candidate) => candidate.key === chosen);
  // Another host: what was typed for the last one is not its model, address or key
  const setChosen = (key: string) => {
    setChosenKey(key);
    setModelId("");
    setBaseUrl("");
    setApiKey("");
  };

  return (
    <section className="setup-card" aria-label="choose a model host">
      <strong>Where should the models run?</strong>
      <div className="setup-choices" role="radiogroup" aria-label="model host">
        {choices.map((candidate) => (
          <label key={candidate.key} className={`setup-choice${chosen === candidate.key ? " on" : ""}`}>
            <input type="radio" name="host" checked={chosen === candidate.key} onChange={() => setChosen(candidate.key)} />
            <span><strong>{candidate.name}</strong>{candidate.key === RECOMMENDED_HOST && <small> recommended</small>}
              {candidate.is_installed && <small className="pill complete setup-installed">{candidate.kind === "api" ? "key set" : "installed"}</small>}
              <br /><small className="muted">{candidate.purpose}</small></span>
          </label>
        ))}
      </div>
      <label className="field">Model
        <input type="text" value={modelId} onChange={(event) => setModelId(event.target.value)} list="setup-host-models"
          placeholder={chosen === RECOMMENDED_HOST ? "empty: the recommended Qwen3.6 35B MTP" : "the model's name on the host"} />
      </label>
      <datalist id="setup-host-models">
        {(runtime?.available_models ?? []).map((model) => <option key={model} value={model} />)}
      </datalist>
      {(runtime?.available_models.length ?? 0) > 0 && (
        <p className="muted">{runtime?.name} has: {runtime?.available_models.join(", ")}</p>
      )}
      {chosen === "mlx" && (
        <label className="field">Server address (base URL)
          <input type="text" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="http://localhost:8080/v1" />
        </label>
      )}
      {runtime?.kind === "api" && (
        <label className="field">{runtime.key_variable} (saved in the git-ignored .env)
          <input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="off" />
        </label>
      )}
      <div className="dialog-actions">
        <button className="btn primary" disabled={isSaving} onClick={async () => {
          setIsSaving(true);
          try {
            const outcome = await sendJson<CommandOutput>("/setup/configure",
              { provider: PROVIDER_OF[chosen], model_id: modelId.trim(), base_url: baseUrl.trim(), api_key: apiKey.trim() });
            onMessage(outcome.output, outcome.exit_code !== 0);
            if (outcome.exit_code === 0) onSaved();
          } catch (error) {
            onMessage(describeError(error), true);
          }
          setIsSaving(false);
        }}>{isSaving ? <><span className="spinner" aria-hidden="true" /> Saving…</>
          : <><Icon name="check" /> Use {runtime?.name ?? "it"} for every phase</>}</button>
      </div>
    </section>
  );
}

function ModelsStep({ check, onStarted, onMessage }: { check: SetupCheck; onStarted: () => Promise<unknown>; onMessage: (text: string, isError?: boolean) => void }) {
  const [token, setToken] = useState("");
  // from the click until the server's check shows the download running, so a second click sends nothing (the ref
  // holds it at once, before the button is drawn disabled)
  const [isStarting, setIsStarting] = useState(false);
  const isStartSent = useRef(false);
  const recommendedMissing = check.models.some((model) => model.recommended_repository && (!model.is_downloaded || model.is_on_host === false));
  // the scanned-page reader's model (3.9 GB, too large for git) comes with the same download while a setting needs it,
  // listed in the table as a model of its own with its state and its Download
  const ocrReader = check.runtimes.find((runtime) => runtime.key === "unlimited-ocr" && runtime.is_needed);
  const ocrReaderMissing = Boolean(ocrReader && !ocrReader.is_installed);
  const isDownloading = isStarting || check.download.is_running;
  const startDownload = async () => {
    if (isStartSent.current) return;
    isStartSent.current = true;
    setIsStarting(true);
    try {
      await sendJson("/setup/models", { hf_token: token.trim() });
      await onStarted();
    } catch (error) {
      onMessage(describeError(error), true);
    } finally {
      isStartSent.current = false;
      setIsStarting(false);
    }
  };
  return (
    <>
      <table className="setup-models">
        <thead><tr><th>Model</th><th>Spec</th><th>Used by</th><th>State</th></tr></thead>
        <tbody>
          {check.models.map((model) => (
            <tr key={model.name}>
              <td>{model.name}</td>
              <td><code>{model.spec || "—"}</code></td>
              <td>{model.used_by.join(", ")}</td>
              <td><span className={`pill ${stateClass(describeModel(model))}`}>{describeModel(model)}</span></td>
            </tr>
          ))}
          {ocrReader && (
            <tr>
              <td>{ocrReader.name}</td>
              <td><code>{ocrReader.model_id || "—"}</code></td>
              <td>scanned PDF pages, phase 1</td>
              <td>
                <div className="setup-model-state">
                  <span className={`pill ${stateClass(ocrReaderMissing ? "not downloaded" : "ready")}`}>
                    {ocrReaderMissing ? "not downloaded" : "ready"}</span>
                  {ocrReaderMissing && (
                    <button className="btn primary small" disabled={isDownloading} onClick={startDownload}>
                      {isDownloading ? <><span className="spinner" aria-hidden="true" /> Downloading…</> : <><Icon name="download" /> Download</>}
                    </button>
                  )}
                </div>
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {check.models.some((model) => model.is_on_host === false && !model.recommended_repository) && (
        <p className="muted">A model that is not recommended is downloaded on its host: in LM Studio by its search, on
          Ollama with <code>ollama pull &lt;model&gt;</code>.</p>
      )}
      {(recommendedMissing || ocrReaderMissing || check.download.is_running) && (
        <section className="setup-card" aria-label="download the recommended models">
          <strong>Download the recommended models</strong>
          <p className="muted">{check.recommended.map((model) => `${model.repository} (${model.used_by})`).join(", ")}:
            downloaded from Hugging Face into this repository's models folder and linked into LM Studio
            (<code>i2k setup-models</code>).</p>
          {ocrReaderMissing && <p className="muted">Unlimited-OCR-2, which reads scanned PDF pages: its tool is built
            and its model (3.9 GB) downloaded into the Hugging Face cache.</p>}
          <label className="field">Hugging Face token (only for a gated model; empty uses HF_TOKEN)
            <input type="password" value={token} onChange={(event) => setToken(event.target.value)} autoComplete="off" />
          </label>
          <div className="dialog-actions">
            <a className="btn" href="https://huggingface.co/settings/tokens" target="_blank" rel="noreferrer">Open the token page</a>
            <button className="btn primary" disabled={isDownloading} onClick={startDownload}>
              {isDownloading ? <><span className="spinner" aria-hidden="true" /> Downloading…</> : <><Icon name="download" /> Download</>}
            </button>
          </div>
        </section>
      )}
      {check.download.log && <pre className="listing" aria-label="download log">{check.download.log}</pre>}
    </>
  );
}

export default function SetupWizard({ check, onClose, onChanged, onMessage }: {
  check: SetupCheck;
  onClose: () => void;
  // reads the check again; a check that cannot be read is said by the page
  onChanged: () => Promise<unknown>;
  onMessage: (text: string, isError?: boolean) => void;
}) {
  // It opens on the first step with a problem, so a missing tool is not three clicks away
  const [step, setStep] = useState<Step>(() => STEPS.find((name) => listStepProblems(check)[name]) ?? "Model host");
  const [system, setSystem] = useState<SetupSystem>(() => detectSystem(navigator.userAgent));
  const [isChoosing, setIsChoosing] = useState(false);
  const [isChecking, setIsChecking] = useState(false);
  const hosts = check.runtimes.filter((runtime) => runtime.kind !== "tool");
  const usedHosts = hosts.filter((runtime) => runtime.is_needed);
  const unusedInstalled = hosts.filter((runtime) => describeRuntime(runtime) === "not configured");
  const tools = check.runtimes.filter((runtime) => runtime.kind === "tool");
  const stepProblem = listStepProblems(check);

  // While a download runs, the check is read again so its log and the models' states follow it
  useEffect(() => {
    if (!check.download.is_running) return;
    const timer = window.setInterval(onChanged, DOWNLOAD_POLL_MS);
    return () => window.clearInterval(timer);
  }, [check.download.is_running, onChanged]);

  const index = STEPS.indexOf(step);
  return (
    <Dialog title="Setup" onClose={onClose}>
      <nav className="setup-nav" aria-label="setup steps">
        {STEPS.map((name, position) => (
          <button key={name} className={`setup-nav-step${name === step ? " on" : ""}`} aria-current={name === step ? "step" : undefined}
            onClick={() => setStep(name)}>
            <span className={`setup-nav-mark ${stepProblem[name] ? "bad" : "ok"}`}>
              {stepProblem[name] ? "!" : <Icon name="check" size={12} />}</span>
            {position + 1}. {name}
          </button>
        ))}
      </nav>

      {step === "Model host" && (
        <>
          {!check.has_models && <p><strong>No model is configured.</strong> Choose where the models run, and which model.</p>}
          {usedHosts.map((runtime) => <RuntimeCard key={runtime.key} runtime={runtime} system={system} onSystem={setSystem} />)}
          {unusedInstalled.length > 0 && (
            <p className="muted">Installed but not configured (no model uses it): {unusedInstalled.map((runtime) => runtime.name).join(", ")}.</p>
          )}
          {(isChoosing || !check.has_models || usedHosts.length === 0)
            ? <HostChooser runtimes={check.runtimes} onMessage={onMessage} onSaved={() => { setIsChoosing(false); onChanged(); }} />
            : <button className="btn ghost small setup-other" onClick={() => setIsChoosing(true)}>Use another model host…</button>}
        </>
      )}
      {step === "Models" && <ModelsStep check={check} onStarted={onChanged} onMessage={onMessage} />}
      {step === "Tools" && tools.map((runtime) => <RuntimeCard key={runtime.key} runtime={runtime} system={system} onSystem={setSystem} />)}

      <div className="dialog-actions">
        <button className="btn ghost" disabled={isChecking} onClick={async () => {
          setIsChecking(true);
          await onChanged();
          setIsChecking(false);
        }}>{isChecking ? <><span className="spinner" aria-hidden="true" /> Checking…</> : <><Icon name="refresh" /> Check again</>}</button>
        <button className="btn" disabled={index === 0} onClick={() => setStep(STEPS[index - 1])}>Back</button>
        {index < STEPS.length - 1
          // a step with its own primary action (choose a host, download models) keeps Next plain beside it
          ? <button className={`btn${stepProblem[step] ? "" : " primary"}`} onClick={() => setStep(STEPS[index + 1])}>Next</button>
          : <button className="btn primary" onClick={onClose}>Done</button>}
      </div>
    </Dialog>
  );
}
