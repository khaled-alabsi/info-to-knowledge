import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "../App";
import type { SetupCheck, SetupRuntime } from "../api";
import { PHASES, installServer } from "../test/fakeServer";
import { describeRuntime, detectSystem, hasSetupProblem } from "./SetupWizard";

function buildRuntime(key: string, kind: SetupRuntime["kind"], facts: Partial<SetupRuntime>): SetupRuntime {
  return {
    key, name: key, kind, purpose: `${key} purpose`, download_url: `https://example.test/${key}`, key_variable: kind === "api" ? "KEY_VAR" : "",
    steps: { mac: [`${key} mac step`], windows: [`${key} windows step`], linux: [`${key} linux step`] },
    start_steps: [`start ${key}`], available_models: [], is_needed: false, is_installed: false, is_running: kind === "host" ? false : null, ...facts,
  };
}

function buildCheck(facts: Partial<SetupCheck>): SetupCheck {
  return {
    has_models: true,
    runtimes: [
      buildRuntime("lmstudio", "host", { is_needed: true, is_installed: false }),
      buildRuntime("ollama", "host", { is_installed: true, is_running: true }),
      buildRuntime("mlx", "host", {}),
      buildRuntime("anthropic", "api", {}),
      buildRuntime("ffmpeg", "tool", { is_needed: true, is_installed: true }),
    ],
    models: [{ name: "frame", spec: "lmstudio:lfm2.5-vl-3b", used_by: ["frames_processing"], is_configured: true,
      is_on_host: null, recommended_repository: "LiquidAI/LFM2.5-VL-3B-GGUF", is_downloaded: false }],
    download: { is_running: false, log: "" },
    recommended: [{ repository: "LiquidAI/LFM2.5-VL-3B-GGUF", spec: "lmstudio:lfm2.5-vl-3b", used_by: "lfm-vl-3b" }],
    ...facts,
  };
}

function serve(check: SetupCheck) {
  return installServer({
    "GET /api/phases": PHASES, "GET /api/inbox": [], "GET /api/runs": [], "GET /api/jobs": [], "GET /api/inbox/trash": [],
    "GET /api/setup": check,
    "POST /api/setup/configure": { exit_code: 0, output: "models.frame.spec saved" },
    "POST /api/setup/models": { pid: 42 },
  });
}

describe("the setup check's words", () => {
  it("tells a missing runtime from one installed that no setting uses", () => {
    expect(describeRuntime(buildRuntime("lmstudio", "host", { is_needed: true }))).toBe("missing");
    expect(describeRuntime(buildRuntime("lmstudio", "host", { is_needed: true, is_installed: true }))).toBe("not running");
    expect(describeRuntime(buildRuntime("lmstudio", "host", { is_needed: true, is_installed: true, is_running: true }))).toBe("ready");
    expect(describeRuntime(buildRuntime("ollama", "host", { is_installed: true }))).toBe("not configured");
    expect(describeRuntime(buildRuntime("ollama", "host", {}))).toBe("not installed");
    expect(describeRuntime(buildRuntime("anthropic", "api", { is_needed: true }))).toBe("key missing");
    expect(describeRuntime(buildRuntime("ffmpeg", "tool", {}))).toBe("not needed");
  });

  it("warns only for what a run needs", () => {
    expect(hasSetupProblem(buildCheck({}))).toBe(true);
    const ready = buildCheck({ runtimes: [buildRuntime("lmstudio", "host", { is_needed: true, is_installed: true, is_running: true }),
      buildRuntime("ollama", "host", {})], models: [] });
    expect(hasSetupProblem(ready)).toBe(false);
    expect(hasSetupProblem({ ...ready, has_models: false })).toBe(true);
  });

  it("picks the system from the browser", () => {
    expect(detectSystem("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)")).toBe("mac");
    expect(detectSystem("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("windows");
    expect(detectSystem("Mozilla/5.0 (X11; Linux x86_64)")).toBe("linux");
  });
});

// A missing part marks the top bar's gear; the Settings page it opens offers Setup needed, which opens the wizard
async function openSetupWizard() {
  await userEvent.click(await screen.findByRole("button", { name: "Settings: setup needed" }));
  await userEvent.click(await screen.findByRole("button", { name: "Setup needed" }));
}

describe("the setup wizard", () => {
  it("marks the settings gear when setup is needed, with no button of its own beside it defect", async () => {
    // "Setup needed" stood as a button beside the settings gear, which the owner found ugly on the phone; it is a mark
    // on the gear now, and the Settings page's wizard button says "Setup needed"
    serve(buildCheck({}));
    render(<App />);
    const gear = await screen.findByRole("button", { name: "Settings: setup needed" });
    expect(gear.querySelector(".alert-mark")).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Setup needed" })).not.toBeInTheDocument();
  });

  it("shows the warning, and the missing host's steps for each system with its download page", async () => {
    serve(buildCheck({}));
    render(<App />);
    await openSetupWizard();
    const card = screen.getByRole("region", { name: "lmstudio" });
    expect(within(card).getByText("missing")).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("tab", { name: "Windows" }));
    expect(within(card).getByText("lmstudio windows step")).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("tab", { name: "Linux" }));
    expect(within(card).getByText("lmstudio linux step")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: /Open the download page/ })).toHaveAttribute("href", "https://example.test/lmstudio");
    // Ollama is installed and running, and no model uses it: not configured, which is no problem
    expect(screen.getByText(/Installed but not configured \(no model uses it\): ollama/)).toBeInTheDocument();
  });

  it("downloads the recommended models with the token typed", async () => {
    const calls = serve(buildCheck({}));
    render(<App />);
    await openSetupWizard();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("host not answering")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/Hugging Face token/), "hf_typed");
    await userEvent.click(screen.getByRole("button", { name: /^Download$/ }));
    expect(calls.find((call) => call.url === "/api/setup/models")?.body).toEqual({ hf_token: "hf_typed" });
  });

  it("lists the scanned-page reader a setting needs in the Models table, with its state and its own Download defect", async () => {
    // The Tools step said the reader was missing and to press Download on the Models step, whose table did not list it:
    // its Download stood in a card under the table. It is a row of the table now, its state and Download on the row
    const calls = serve(buildCheck({
      runtimes: [buildRuntime("lmstudio", "host", { is_needed: true, is_installed: true, is_running: true }),
        { ...buildRuntime("unlimited-ocr", "tool", { is_needed: true, is_installed: false }),
          model_id: "mlx-community/Unlimited-OCR-2-8bit" }],
      models: [{ name: "frame", spec: "lmstudio:lfm2.5-vl-3b", used_by: ["frames_processing"], is_configured: true,
        is_on_host: true, recommended_repository: "LiquidAI/LFM2.5-VL-3B-GGUF", is_downloaded: true }],
    }));
    render(<App />);
    await openSetupWizard();
    // it opens on Models, the first step with a problem: the reader's model is not downloaded, and the step is marked
    const models = within(screen.getByRole("navigation", { name: "setup steps" })).getByRole("button", { name: /Models/ });
    expect(models).toHaveAttribute("aria-current", "step");
    expect(models.textContent).toContain("!");
    const row = screen.getByRole("row", { name: /mlx-community\/Unlimited-OCR-2-8bit/ });
    expect(within(row).getByText("not downloaded")).toBeInTheDocument();
    await userEvent.click(within(row).getByRole("button", { name: /^Download$/ }));
    expect(calls.filter((call) => call.url === "/api/setup/models")).toHaveLength(1);
  });

  it("offers no download when the reader is installed or no setting needs it", async () => {
    serve(buildCheck({
      runtimes: [buildRuntime("lmstudio", "host", { is_needed: true, is_installed: true, is_running: true }),
        buildRuntime("unlimited-ocr", "tool", { is_needed: false, is_installed: false })],
      models: [{ name: "frame", spec: "lmstudio:lfm2.5-vl-3b", used_by: ["frames_processing"], is_configured: true,
        is_on_host: true, recommended_repository: "LiquidAI/LFM2.5-VL-3B-GGUF", is_downloaded: true }],
    }));
    render(<App />);
    expect(screen.queryByRole("button", { name: /Setup needed/ })).not.toBeInTheDocument();
  });

  it("sends one download however often Download is clicked before the server answers", async () => {
    let answer = (_value: { pid: number }) => {};
    const calls = installServer({
      "GET /api/phases": PHASES, "GET /api/inbox": [], "GET /api/runs": [], "GET /api/jobs": [], "GET /api/inbox/trash": [],
      "GET /api/setup": buildCheck({}),
      // the server is slow to start the download: both clicks land before it answers
      "POST /api/setup/models": () => new Promise<{ pid: number }>((resolve) => { answer = resolve; }),
    });
    render(<App />);
    await openSetupWizard();
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    const download = screen.getByRole("button", { name: /^Download$/ });
    await userEvent.dblClick(download);
    expect(calls.filter((call) => call.url === "/api/setup/models")).toHaveLength(1);
    answer({ pid: 42 });
  });

  it("opens by itself when no model is configured, and saves the host and model chosen", async () => {
    const calls = serve(buildCheck({ has_models: false, models: [] }));
    render(<App />);
    expect(await screen.findByText("No model is configured.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: /ollama/ }));
    await userEvent.type(screen.getByLabelText("Model"), "qwen3-vl:8b");
    await userEvent.click(screen.getByRole("button", { name: /Use ollama for every phase/ }));
    expect(calls.find((call) => call.url === "/api/setup/configure")?.body)
      .toEqual({ provider: "ollama", model_id: "qwen3-vl:8b", base_url: "", api_key: "" });
    expect(await screen.findByText("models.frame.spec saved")).toBeInTheDocument();
  });

  it("asks for an API's key when an API is chosen", async () => {
    const calls = serve(buildCheck({ has_models: false, models: [] }));
    render(<App />);
    await userEvent.click(await screen.findByRole("radio", { name: /anthropic/ }));
    await userEvent.type(screen.getByLabelText("Model"), "claude-test");
    await userEvent.type(screen.getByLabelText(/KEY_VAR/), "sk-typed");
    await userEvent.click(screen.getByRole("button", { name: /Use anthropic for every phase/ }));
    expect(calls.find((call) => call.url === "/api/setup/configure")?.body)
      .toEqual({ provider: "anthropic", model_id: "claude-test", base_url: "", api_key: "sk-typed" });
  });

  it("shows no warning when everything a run needs is there", async () => {
    serve(buildCheck({ runtimes: [buildRuntime("lmstudio", "host", { is_needed: true, is_installed: true, is_running: true })], models: [] }));
    render(<App />);
    expect(await screen.findByText(/Nothing open/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Setup needed/ })).not.toBeInTheDocument();
  });
});

describe("a host installed with its server off", () => {
  it("shows only how to start it, with no install steps and no download page", async () => {
    serve(buildCheck({ runtimes: [buildRuntime("lmstudio", "host", { is_needed: true, is_installed: true, is_running: false })] }));
    render(<App />);
    await openSetupWizard();
    const card = screen.getByRole("region", { name: "lmstudio" });
    expect(within(card).getByText("not running")).toBeInTheDocument();
    expect(within(card).getByText("start lmstudio")).toBeInTheDocument();
    expect(within(card).queryByText("lmstudio mac step")).not.toBeInTheDocument();
    expect(within(card).queryByRole("link")).not.toBeInTheDocument();
  });
});

describe("a runtime whose steps are the same on every system", () => {
  it("shows its steps with no system tabs", async () => {
    const same = { mac: ["copy it"], windows: ["copy it"], linux: ["copy it"] };
    serve(buildCheck({ runtimes: [buildRuntime("classifier", "tool", { is_needed: true, steps: same, download_url: "" })] }));
    render(<App />);
    await openSetupWizard();
    await userEvent.click(screen.getByRole("button", { name: /3\. Tools/ }));
    const card = screen.getByRole("region", { name: "classifier" });
    expect(within(card).getByText("copy it")).toBeInTheDocument();
    expect(within(card).queryByRole("tab")).not.toBeInTheDocument();
  });
});

describe("choosing a host", () => {
  it("starts on an installed host, offers its models, and clears what was typed for another host", async () => {
    const runtimes = [buildRuntime("lmstudio", "host", {}), buildRuntime("ollama", "host", { is_installed: true, is_running: true, available_models: ["qwen3-vl:8b"] }),
      buildRuntime("anthropic", "api", {})];
    serve(buildCheck({ has_models: false, models: [], runtimes }));
    render(<App />);
    expect(await screen.findByRole("radio", { name: /ollama/ })).toBeChecked();
    expect(screen.getByText("ollama has: qwen3-vl:8b")).toBeInTheDocument();
    expect(within(screen.getByRole("radio", { name: /ollama/ }).closest("label") as HTMLElement).getByText("installed")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Model"), "qwen3-vl:8b");
    await userEvent.click(screen.getByRole("radio", { name: /anthropic/ }));
    expect(screen.getByLabelText("Model")).toHaveValue("");
  });
});
