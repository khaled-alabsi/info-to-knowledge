import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "../App";
import type { SettingRow } from "../api";
import { PHASES, answerText, installServer } from "../test/fakeServer";
import { saveDrafts } from "./SettingsView";

const ROWS = [
  { key: "budgets.maxAttempts", value: 3, kind: "number", source: "local", comment: "Answers a step may give" },
  { key: "review.isOn", value: true, kind: "bool", source: "code", comment: "Whether phase 5 runs" },
  { key: "models.frame.sampling", value: { top_k: 20 }, kind: "json", source: "local", comment: "" },
  { key: "frames.boardHandler", value: "text-diff-integration", kind: "text", source: "code", comment: "How phase 3 is shown a board step",
    choices: ["image-diff-only", "text-diff-integration"] },
  { key: "models.small.v2.spec", path: ["models", "small.v2", "spec"], value: "lmstudio:small-model", kind: "text", source: "local",
    comment: "", choices: [], is_open_choice: true, chosen_address: "http://localhost:1234/v1",
    default_address: "http://localhost:1234/v1", choice_options: [
      { value: "lmstudio:big-model", label: "big-model", hint: "", group: "LM Studio at http://other-mac:1234/v1",
        address: "http://other-mac:1234/v1" },
      { value: "lmstudio:big-model", label: "big-model", hint: "", group: "LM Studio at http://localhost:1234/v1",
        address: "http://localhost:1234/v1" },
      { value: "lmstudio:small-model", label: "small-model", hint: "", group: "LM Studio at http://localhost:1234/v1",
        address: "http://localhost:1234/v1" }] },
  { key: "models.small.v2.baseUrl", path: ["models", "small.v2", "baseUrl"], value: null, kind: "text", source: "code",
    comment: "", choices: [], is_open_choice: true, choice_options: [
      { value: "", label: "(empty)", hint: "its default", group: "" },
      { value: "http://other-mac:1234/v1", label: "http://other-mac:1234/v1", hint: "profile other", group: "" }] },
  { key: "models.small.v2.temperature", path: ["models", "small.v2", "temperature"], value: 0.7, kind: "number",
    source: "local", comment: "" },
];

function serveSettings(save: (body: { key: string }) => unknown = () => ({ exit_code: 0, output: "saved" }), serverProfile = "") {
  return installServer({
    "GET /api/phases": PHASES, "GET /api/inbox": [], "GET /api/runs": [], "GET /api/jobs": [], "GET /api/inbox/trash": [],
    "GET /api/profiles": { profiles: ["strict"], server_profile: serverProfile },
    "GET /api/settings": ROWS,
    "GET /api/settings?profile=strict": [{ ...ROWS[0], value: 1, source: "strict" }],
    "PUT /api/settings": (body) => save(body as { key: string }),
  });
}

async function openSettings() {
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: /Settings/ }));
  return screen.findByRole("spinbutton", { name: "budgets.maxAttempts" });
}

describe("Settings", () => {
  it("calls the first entry the default settings and marks the server's own profile by its name defect", async () => {
    // The first entry read "the server's settings (profile strict)" while it showed the default settings without the
    // profile, so switching to the profile seemed to change nothing. It now reads "default settings"
    serveSettings(undefined, "strict");
    await openSettings();
    await userEvent.click(screen.getByRole("button", { name: "Edit: default settings" }));
    expect(screen.getAllByRole("menuitemradio").map((item) => item.textContent)).toEqual([
      "default settingsboth default settings files", "profile: strictthe server runs on it"]);
    expect(screen.queryByText(/the server's settings/)).not.toBeInTheDocument();
  });

  it("offers a model's spec as a menu of each host's models under the host's heading, the chosen one ticked", async () => {
    const calls = serveSettings();
    await openSettings();
    await userEvent.click(screen.getByRole("button", { name: "models.small.v2.spec: small-model" }));
    const menu = screen.getByRole("menu", { name: "models.small.v2.spec" });
    expect(within(menu).getAllByRole("none").map((item) => item.textContent)).toEqual([
      "LM Studio at http://other-mac:1234/v1", "big-model", "LM Studio at http://localhost:1234/v1", "big-model",
      "small-model", "Other…type a value of your own"]);
    expect(within(menu).getByRole("menuitemradio", { name: "small-model" })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(within(menu).getAllByRole("menuitemradio", { name: "big-model" })[1]);
    expect(screen.getByRole("button", { name: "models.small.v2.spec: big-model" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save settings (1)" }));
    expect(calls.filter((call) => call.method === "PUT").map((call) => call.body)).toEqual([
      { key: "models.small.v2.spec", new_value: "lmstudio:big-model", profile: "", key_parts: ["models", "small.v2", "spec"] }]);
  });

  it("ticks a model only under the host it is called at, and choosing it under another host moves its address defect", async () => {
    // A model two hosts serve was ticked under both hosts, so the menu did not say which host the model is called at.
    // Only the entry under the model's address is ticked now, and choosing one under another host sets that address
    const calls = serveSettings();
    await openSettings();
    await userEvent.click(screen.getByRole("button", { name: "models.small.v2.spec: small-model" }));
    await userEvent.click(within(screen.getByRole("menu")).getAllByRole("menuitemradio", { name: "big-model" })[0]);
    await userEvent.click(screen.getByRole("button", { name: "models.small.v2.spec: big-model" }));
    const ticks = within(screen.getByRole("menu")).getAllByRole("menuitemradio", { name: "big-model" })
      .map((item) => item.getAttribute("aria-checked"));
    expect(ticks).toEqual(["true", "false"]);
    expect(screen.getByRole("button", { name: "models.small.v2.baseUrl: http://other-mac:1234/v1" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save settings (2)" }));
    expect(calls.filter((call) => call.method === "PUT").map((call) => call.body)).toEqual([
      { key: "models.small.v2.spec", new_value: "lmstudio:big-model", profile: "", key_parts: ["models", "small.v2", "spec"] },
      { key: "models.small.v2.baseUrl", new_value: "http://other-mac:1234/v1", profile: "",
        key_parts: ["models", "small.v2", "baseUrl"] }]);
  });

  it("takes a value of one's own from Other…, saved by its path", async () => {
    const calls = serveSettings();
    await openSettings();
    expect(screen.queryByRole("textbox", { name: /your own value/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "models.small.v2.spec: small-model" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Other…/ }));
    const typed = screen.getByRole("textbox", { name: "models.small.v2.spec, your own value" });
    await userEvent.clear(typed);
    await userEvent.type(typed, "lmstudio:not-yet-downloaded");
    await userEvent.click(screen.getByRole("button", { name: "Save settings (1)" }));
    expect(calls.filter((call) => call.method === "PUT").map((call) => call.body)).toEqual([
      { key: "models.small.v2.spec", new_value: "lmstudio:not-yet-downloaded", profile: "",
        key_parts: ["models", "small.v2", "spec"] }]);
  });

  it("adds a model as a copy of another's set fields, a draft until Save settings writes it", async () => {
    const calls = serveSettings();
    await openSettings();
    await userEvent.click(screen.getByRole("heading", { name: "Models" }));
    await userEvent.click(screen.getByRole("button", { name: "+ Add model" }));
    await userEvent.type(screen.getByRole("textbox", { name: "New model's name" }), "small.v2");
    expect(screen.getByText("a model has this name")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
    await userEvent.clear(screen.getByRole("textbox", { name: "New model's name" }));
    await userEvent.type(screen.getByRole("textbox", { name: "New model's name" }), "other-mac");
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    expect(JSON.parse((screen.getByRole("textbox", { name: "models.other-mac" }) as HTMLTextAreaElement).value))
      .toEqual({ spec: "lmstudio:small-model", temperature: 0.7 });
    await userEvent.click(screen.getByRole("button", { name: "Save settings (1)" }));
    expect(calls.filter((call) => call.method === "PUT").map((call) => call.body)).toEqual([
      { key: "models.other-mac", new_value: { spec: "lmstudio:small-model", temperature: 0.7 }, profile: "",
        key_parts: ["models", "other-mac"] }]);
  });

  it("keeps each change as a marked draft, and Save settings in the top bar writes them all", async () => {
    const calls = serveSettings();
    const field = await openSettings();
    expect(screen.getByText("Answers a step may give")).toBeInTheDocument();
    expect(within(field.closest(".setting-row") as HTMLElement).getByText("local")).toBeInTheDocument();
    await userEvent.clear(field);
    await userEvent.type(field, "4");
    await userEvent.click(screen.getByRole("checkbox", { name: "review.isOn" }));
    expect(calls.some((call) => call.method === "PUT")).toBe(false);
    expect(field.closest(".setting-row")).toHaveClass("edited");
    const save = screen.getByRole("button", { name: "Save settings (2)" });
    await userEvent.click(save);
    expect(calls.filter((call) => call.method === "PUT").map((call) => call.body)).toEqual([
      { key: "budgets.maxAttempts", new_value: 4, profile: "", key_parts: null },
      { key: "review.isOn", new_value: false, profile: "", key_parts: null }]);
    expect(await screen.findByText("2 settings saved; the next run reads them")).toBeInTheDocument();
    // Still open, with nothing left to save: the button says how to leave
    expect(screen.getByRole("button", { name: /Close settings/ })).toBeInTheDocument();
  });

  it("offers a setting whose type names its values as a list, and saves the one chosen", async () => {
    const calls = serveSettings();
    await openSettings();
    await userEvent.click(screen.getByRole("button", { name: "frames.boardHandler: text-diff-integration" }));
    const list = screen.getByRole("menu", { name: "frames.boardHandler" });
    expect(within(list).getAllByRole("menuitemradio").map((option) => option.textContent)).toEqual(["image-diff-only", "text-diff-integration"]);
    await userEvent.click(within(list).getByRole("menuitemradio", { name: "image-diff-only" }));
    await userEvent.click(screen.getByRole("button", { name: "Save settings (1)" }));
    expect(calls.filter((call) => call.method === "PUT").map((call) => call.body)).toEqual([
      { key: "frames.boardHandler", new_value: "image-diff-only", profile: "", key_parts: null }]);
  });

  it("a change typed back to the saved value is no draft, and a refused or unreadable one stays a draft", async () => {
    serveSettings(() => ({ exit_code: 2, output: "budgets.maxAttempts = -1 is refused: too small" }));
    const field = await openSettings();
    await userEvent.clear(field);
    await userEvent.type(field, "3");
    expect(screen.queryByRole("button", { name: /Save settings/ })).not.toBeInTheDocument();
    await userEvent.clear(field);
    await userEvent.type(field, "-1");
    const sampling = screen.getByRole("textbox", { name: "models.frame.sampling" });
    await userEvent.clear(sampling);
    await userEvent.type(sampling, "not json");
    await userEvent.click(screen.getByRole("button", { name: "Save settings (2)" }));
    expect(await screen.findByText(/is refused: too small · models.frame.sampling: not valid JSON/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save settings (2)" })).toBeInTheDocument();
  });

  it("names the setting a failed save belongs to, and calls only a value typed as JSON not valid JSON", async () => {
    const number = { row: ROWS[0] as SettingRow, raw: "4" };
    installServer({ "PUT /api/settings": answerText("<html>proxy page</html>") });
    const unreadable = await saveDrafts({ "budgets.maxAttempts": number }, "");
    expect(unreadable.message).toBe("budgets.maxAttempts: 200: the server's answer was not readable");
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    expect((await saveDrafts({ "budgets.maxAttempts": number }, "")).message).toBe("budgets.maxAttempts: Failed to fetch");
  });

  it("shows every group closed, opens the groups a search matches, and a profile's files drop the drafts", async () => {
    serveSettings();
    await openSettings();
    const budgets = screen.getByRole("heading", { name: "Budgets" }).closest("details")!;
    expect(budgets.open).toBe(false);
    await userEvent.click(screen.getByRole("heading", { name: "Budgets" }));
    expect(budgets.open).toBe(true);
    await userEvent.type(screen.getByRole("searchbox"), "phase 5");
    expect(screen.getByRole("heading", { name: "Review" }).closest("details")!.open).toBe(true);
    expect(screen.queryByRole("heading", { name: "Budgets" })).not.toBeInTheDocument();
    await userEvent.clear(screen.getByRole("searchbox"));
    const field = screen.getByRole("spinbutton", { name: "budgets.maxAttempts" });
    await userEvent.clear(field);
    await userEvent.type(field, "5");
    expect(screen.getByRole("button", { name: "Save settings (1)" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Edit: default settings" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: "profile: strict" }));
    await userEvent.click(screen.getByRole("button", { name: "Discard the changes and switch" }));
    expect(await screen.findByText("profile strict")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save settings/ })).not.toBeInTheDocument();
  });

  it("names what a chosen profile sets, so a switch that changes only those rows is seen defect", async () => {
    // Choosing a profile that sets two model addresses left every row in view as it was, and the owner read it as the
    // switch changing nothing. The page now names the settings the profile sets and marks their rows "profile <name>"
    serveSettings();
    await openSettings();
    await userEvent.click(screen.getByRole("button", { name: "Edit: default settings" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: "profile: strict" }));
    expect(await screen.findByText(/Profile strict sets 1 setting:/)).toHaveTextContent(
      "Profile strict sets 1 setting: budgets.maxAttempts. Every other value is the default settings'.");
    expect(screen.getByText("profile strict")).toBeInTheDocument();
  });

  it("asks before a switch drops unsaved changes, and switches at once with none", async () => {
    serveSettings();
    await openSettings();
    const field = screen.getByRole("spinbutton", { name: "budgets.maxAttempts" });
    await userEvent.clear(field);
    await userEvent.type(field, "5");
    await userEvent.click(screen.getByRole("button", { name: "Edit: default settings" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: "profile: strict" }));
    expect(screen.getByRole("alert")).toHaveTextContent("You have 1 unsaved change to the default settings");
    await userEvent.click(screen.getByRole("button", { name: "Stay on the default settings" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save settings (1)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit: default settings" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    await userEvent.click(screen.getByRole("button", { name: "Edit: default settings" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: "profile: strict" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(await screen.findByText(/Profile strict sets/)).toBeInTheDocument();
  });
});
