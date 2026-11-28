import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "../App";
import type { SettingRow } from "../api";
import { PHASES, answerText, installServer } from "../test/fakeServer";
import { saveDrafts } from "./SettingsView";

const ROWS = [
  { key: "budgets.maxAttempts", value: 3, kind: "number", source: "local", comment: "Answers a step may give" },
  // a setting nobody changed is not on the page, so the ones the tests look for are the owner's own (source "local")
  { key: "review.isOn", value: true, kind: "bool", source: "local", comment: "Whether phase 5 runs" },
  { key: "models.frame.sampling", value: { top_k: 20 }, kind: "json", source: "local", comment: "" },
  { key: "frames.boardHandler", value: "text-diff-integration", kind: "text", source: "local", comment: "How phase 3 is shown a board step",
    choices: ["image-diff-only", "text-diff-integration"] },
  { key: "models.small.v2.spec", path: ["models", "small.v2", "spec"], value: "lmstudio:small-model", kind: "text", source: "local",
    comment: "", choices: [], is_open_choice: true, chosen_host: "lmstudio", default_host: "lmstudio", choice_options: [
      { value: "openai-compatible:big-model", label: "big-model", hint: "", group: "other-mac", host: "other-mac" },
      { value: "lmstudio:big-model", label: "big-model", hint: "", group: "lmstudio", host: "lmstudio" },
      { value: "lmstudio:small-model", label: "small-model", hint: "", group: "lmstudio", host: "lmstudio" }] },
  { key: "models.small.v2.host", path: ["models", "small.v2", "host"], value: "", kind: "text", source: "code",
    comment: "", choices: [], is_open_choice: true, choice_options: [
      { value: "", label: "(empty)", hint: "its default", group: "" },
      { value: "lmstudio", label: "lmstudio", hint: "", group: "" },
      { value: "other-mac", label: "other-mac", hint: "", group: "" }] },
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
  return screen.findByRole("spinbutton", { name: "Tries per step" });
}

describe("Settings", () => {
  const SET_ROWS = [...ROWS,
    { key: "providerSets", value: { PC: { machine: "the desktop", phaseModels: { review: "small.v2" }, budgets: { maxAttempts: 5 } } },
      kind: "json", source: "local", comment: "" },
    { key: "defaultProviderSet", value: "default", kind: "text", source: "code", comment: "" },
    { key: "machine", value: "the laptop", kind: "text", source: "local", comment: "" },
    { key: "hosts", value: { lmstudio: { url: "http://localhost:1234/v1" }, "other-mac": { url: "http://other-mac:1234/v1" } },
      kind: "json", source: "local", comment: "" },
    { key: "models.frame.spec", path: ["models", "frame", "spec"], value: "lmstudio:big-model", kind: "text", source: "code", comment: "" },
    { key: "phaseModels.review", path: ["phaseModels", "review"], value: "", kind: "text", source: "code", comment: "",
      resolved: "frame", resolved_note: "the phase's own model" }];

  function serveSets(save: (body: { key: string }) => unknown = () => ({ exit_code: 0, output: "saved" })) {
    return installServer({
      "GET /api/phases": PHASES, "GET /api/inbox": [], "GET /api/runs": [], "GET /api/jobs": [], "GET /api/inbox/trash": [],
      "GET /api/profiles": { profiles: [], server_profile: "" },
      "GET /api/settings": SET_ROWS,
      "PUT /api/settings": (body) => save(body as { key: string }),
    });
  }

  it("lists the model servers, then the provider sets, before every other section, each header a title and a count of its cards defect", async () => {
    // The owner: "I still see the model section" and "where 1 from?": the page has two model sections, the servers (each card
    // holding its models) and the provider sets, and the number beside a title counts the cards the section shows
    serveSets();
    await openSettings();
    const titles = screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent);
    expect(titles.slice(0, 2)).toEqual(["Model servers", "Provider sets"]);
    for (const hidden of ["General", "Machines", "Models", "Model providers", "Model providers and models"]) expect(titles).not.toContain(hidden);
    // a header is its title and its card count, nothing else: two servers, and the default set with the second set
    const header = (title: string) => screen.getByRole("heading", { name: title }).closest("summary") as HTMLElement;
    expect(header("Model servers").textContent).toBe("Model servers2");
    expect(header("Provider sets").textContent).toBe("Provider sets2");
    expect(document.body.textContent).not.toMatch(/\d+ changed|· \d/);
  });

  it("shows no group heading that repeats its section's title, and no second help paragraph under the servers defect", async () => {
    // The owner: "frames frames?": a section's first group heading said its own title again
    serveSets();
    await openSettings();
    await userEvent.click(screen.getByRole("heading", { name: "Model servers" }));
    const servers = screen.getByRole("group", { name: "Model servers" }) as HTMLElement;
    expect(within(servers).queryByRole("heading", { name: "Providers" })).not.toBeInTheDocument();
    expect(servers.querySelector(".settings-section-intro")).toBeNull();
    expect(servers.firstElementChild?.nextElementSibling?.className).toBe("server-cards");
    for (const section of screen.getAllByRole("group").filter((each) => each.classList.contains("settings-section"))) {
      const title = section.querySelector("summary h3")?.textContent ?? "";
      const inner = Array.from(section.querySelectorAll(".settings-prefix")).map((each) => each.textContent);
      expect(inner).not.toContain(title);
    }
  });

  it("says a server holds no models yet and names its login key in plain words, never an empty card or a variable name defect", async () => {
    // A server with no models showed an empty card, and its login field was "Token variable" with a code-name placeholder.
    // The card now says "No models yet", and the login field is "Login key name" with the placeholder "Empty if no login".
    serveSets();
    await openSettings();
    await userEvent.click(screen.getByRole("heading", { name: "Model servers" }));
    const card = screen.getByRole("group", { name: "server other-mac" });
    expect(within(card).getByText("No models yet.")).toBeInTheDocument();
    expect(within(card).getByText("Login key name")).toBeInTheDocument();
    expect(within(card).getByRole("textbox", { name: "login key name of other-mac" })).toHaveAttribute("placeholder", "Empty if no login");
    expect(card.textContent).not.toMatch(/Token variable|environment variable/i);
  });

  it("offers a setting with a fixed set of values as a menu in plain words, and names every control by its label defect", async () => {
    // The speech engine's size was a typing box holding "large-v3-turbo", and a control was named by its settings key.
    // It is now a menu naming "Large, fast version"; the control is named "Speech model size", never "audio.whisperModel".
    const calls = installServer({
      "GET /api/phases": PHASES, "GET /api/inbox": [], "GET /api/runs": [], "GET /api/jobs": [], "GET /api/inbox/trash": [],
      "GET /api/profiles": { profiles: [], server_profile: "" },
      "GET /api/settings": [...SET_ROWS, { key: "audio.whisperModel", path: ["audio", "whisperModel"], value: "large-v3-turbo",
        kind: "text", source: "local", comment: "" }],
      "PUT /api/settings": () => ({ exit_code: 0, output: "saved" }),
    });
    await openSettings();
    await userEvent.type(screen.getByRole("searchbox"), "whisper");
    expect(screen.queryByRole("textbox", { name: /whisperModel/ })).not.toBeInTheDocument();
    const button = await screen.findByRole("button", { name: /: Large, fast version$/ });
    expect(button.getAttribute("aria-label")).not.toMatch(/audio\./);
    await userEvent.click(button);
    await userEvent.click(screen.getByRole("menuitemradio", { name: /^Small/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save settings (1)" }));
    expect(calls.filter((call) => call.method === "PUT").map((call) => (call.body as { new_value: string }).new_value)).toEqual(["small"]);
  });

  it("splits the Frames section into Frame capturing, Frames preparing and Frames processing cards, never one lump defect", async () => {
    // The owner: "frames frames?": 52 settings stood under a heading named like the section. The two steps are named as the
    // jobs name them ("Frames preparing", "Frames processing"). The owner's own settings are what the section shows
    const row = (key: string) => ({ key, path: key.split("."), value: 1, kind: "number", source: "local", comment: "" });
    installServer({
      "GET /api/phases": PHASES, "GET /api/inbox": [], "GET /api/runs": [], "GET /api/jobs": [], "GET /api/inbox/trash": [],
      "GET /api/profiles": { profiles: [], server_profile: "" },
      "GET /api/settings": [...SET_ROWS, row("frames.perMinute"), row("frames.isKnowledgeFilterOn"), row("framesProcessing.repairCalls"),
        row("prompt.previousFramesLimit"), row("frames.stillSeconds"), row("framesProcessing.framesPerCall"),
        row("frames.isBoardStageOn")],
    });
    await openSettings();
    await userEvent.click(screen.getByRole("heading", { name: "Frames" }));
    const section = screen.getByRole("group", { name: "Frames" }) as HTMLElement;
    const cards = Array.from(section.querySelectorAll(".settings-prefix")).map((each) => each.textContent);
    expect(cards).toEqual(["Frame capturing", "Screen watching", "Frames preparing", "Text and boards", "What the model is told",
      "Frames processing"]);
    // an opened section shows its card titles alone: every card is closed until it is opened, and no setting is on the page
    const details = Array.from(section.querySelectorAll("details.settings-prefix-group")) as HTMLDetailsElement[];
    expect(details.map((each) => each.open)).toEqual(details.map(() => false));
    // the section's header counts the cards it shows
    expect(within(section).getAllByText("6").length).toBeGreaterThan(0);
    const last = details.length - 1;
    await userEvent.click(within(section).getByText("Frames processing"));
    expect(details.map((each) => each.open)).toEqual(details.map((_, index) => index === last));
    expect(within(details[last]).getAllByRole("spinbutton")).toHaveLength(2);
  });

  it("lists only the settings the owner changed, and finds any other one by search defect", async () => {
    // Defect as shown: about 150 settings nobody changed stood in six sections beside the few the owner changed. The fix: a
    // setting still at the program's or the shipped default shows only when a search finds it or a change to it is unsaved
    const row = (key: string, source: string) => ({ key, path: key.split("."), value: 1, kind: "number", source, comment: "" });
    installServer({
      "GET /api/phases": PHASES, "GET /api/inbox": [], "GET /api/runs": [], "GET /api/jobs": [], "GET /api/inbox/trash": [],
      "GET /api/profiles": { profiles: [], server_profile: "" },
      "GET /api/settings": [...SET_ROWS, row("frames.perMinute", "code"), row("audio.mlx.chunkSeconds", "default"),
        row("frames.stillSeconds", "local")],
    });
    await openSettings();
    const titles = screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent);
    expect(titles).not.toContain("Audio");
    expect(screen.queryByText("Frame capturing")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Frames" })).toBeInTheDocument();
    await userEvent.type(screen.getByRole("searchbox"), "Audio per call");
    expect(await screen.findByRole("heading", { name: "Audio" })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Audio per call (seconds)" })).toBeInTheDocument();
  });

  it("starts every section and card shut on every load, whatever an earlier visit left open defect", async () => {
    // Defect as shown: the open sections were remembered, so the page opened with groups already open. The fix: shut on load
    window.localStorage.setItem("settingsSections", JSON.stringify(["servers", "frames"]));
    window.localStorage.setItem("settingsCards", JSON.stringify(["frames/Frame capturing"]));
    serveSets();
    await openSettings();
    const open = Array.from(document.querySelectorAll("details.settings-section, details.settings-prefix-group"))
      .filter((each) => (each as HTMLDetailsElement).open);
    expect(open).toHaveLength(0);
  });

  it("names the two sections while the settings load, never only a spinner defect", async () => {
    // Defect as shown: the first load showed a spinner and nothing else. The fix: the two sections stand there by name
    installServer({
      "GET /api/phases": PHASES, "GET /api/inbox": [], "GET /api/runs": [], "GET /api/jobs": [], "GET /api/inbox/trash": [],
      "GET /api/profiles": { profiles: [], server_profile: "" },
      "GET /api/settings": () => new Promise<never>(() => undefined),
    });
    render(<App />);
    await userEvent.click(await screen.findByRole("button", { name: /Settings/ }));
    expect(await screen.findByText("Model servers")).toBeInTheDocument();
    expect(screen.getByText("Provider sets")).toBeInTheDocument();
  });

  it("has two model sections: servers holding their models, and sets mapping each job to a model, in plain words defect", async () => {
    // The owner, third time: "models section is wrong, I see no logic for it". The providers section held 20 loose rows (a
    // speech server, its model, its wait, a library model, ...); a card showed backticked keys. The servers section is now
    // one card a server with its models inside, every job is a menu in a provider set, and no help shows a code key
    const comment = "The `libraryModel` of `providers.speech` over `host` and `timeoutS` below. \"\" keeps `host`";
    const providerRows = ["chat", "speech", "diarization", "embeddings", "decision"].flatMap((kind) => [
      { key: `providers.${kind}.libraryModel`, path: ["providers", kind, "libraryModel"], value: "", kind: "text", source: "code", comment },
      { key: `providers.${kind}.host`, path: ["providers", kind, "host"], value: "", kind: "text", source: "code", comment },
      { key: `providers.${kind}.timeoutS`, path: ["providers", kind, "timeoutS"], value: 0, kind: "number", source: "code", comment },
      ...(kind === "chat" ? [] : [{ key: `providers.${kind}.model`, path: ["providers", kind, "model"], value: "", kind: "text",
        source: "code", comment }])]);
    installServer({
      "GET /api/phases": PHASES, "GET /api/inbox": [], "GET /api/runs": [], "GET /api/jobs": [], "GET /api/inbox/trash": [],
      "GET /api/profiles": { profiles: [], server_profile: "" },
      "GET /api/settings": [...SET_ROWS, ...providerRows,
        { key: "models.frame.suits", path: ["models", "frame", "suits"], value: ["frames"], kind: "json", source: "default", comment },
        { key: "video.fastProcessing.frameModel", path: ["video", "fastProcessing", "frameModel"], value: "", kind: "text",
          source: "default", comment },
        { key: "video.fastProcessing.documentModel", path: ["video", "fastProcessing", "documentModel"], value: "", kind: "text",
          source: "default", comment },
        { key: "video.fastProcessing.frameFollowUps", path: ["video", "fastProcessing", "frameFollowUps"], value: 4, kind: "number",
          source: "default", comment }],
    });
    await openSettings();
    const titles = screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent);
    expect(titles.slice(0, 2)).toEqual(["Model servers", "Provider sets"]);
    for (const title of ["Model servers", "Provider sets"]) await userEvent.click(screen.getByRole("heading", { name: title }));
    const servers = screen.getByRole("group", { name: "Model servers" }) as HTMLElement;
    // the servers section holds each server's card with its models inside, and nothing about a job
    expect(within(servers).getAllByRole("group", { name: /^server / })).toHaveLength(2);
    const lmstudio = within(servers).getByRole("group", { name: "server lmstudio" });
    expect(within(lmstudio).getByRole("group", { name: "model frame" })).toBeInTheDocument();
    expect(within(lmstudio).getByRole("textbox", { name: "address of lmstudio" })).toHaveValue("http://localhost:1234/v1");
    // a job is chosen in a provider set, never in a server's card
    expect(within(servers).queryByRole("button", { name: /^default: / })).not.toBeInTheDocument();
    // the default set holds every job; a job no model can do has no menu to open (it names what it runs on instead)
    const fallback = screen.getByRole("group", { name: "provider set default" });
    for (const job of ["Speech to text", "Scanned page reader", "Speaker detection", "Frame vectors", "Decision filter", "Review"]) {
      expect(within(fallback).getByText(job)).toBeInTheDocument();
    }
    expect(within(fallback).getByRole("button", { name: /^default: Review/ })).toBeInTheDocument();
    expect(within(fallback).queryByRole("button", { name: /^default: Speech to text/ })).not.toBeInTheDocument();
    // the fast workflow keeps its flow settings (found by search) and has no model menus
    await userEvent.type(screen.getByRole("searchbox"), "Frames per session");
    expect(document.body.textContent).toMatch(/Frames per session/);
    expect(document.body.textContent).not.toMatch(/Frame describer|Document writer/);
    const fast = screen.getByRole("group", { name: "Describing frames" });
    expect(within(fast).getByText("Frames per session")).toBeInTheDocument();
    expect(within(fast).queryByRole("button")).not.toBeInTheDocument();
    // no code key, backtick or pair of quotes reaches the page, and no "Library model"
    // each piece of text on its own: textContent glues neighbouring elements together into words that are no one's
    const pieces: string[] = [];
    const walker = document.createTreeWalker(document.querySelector(".settings-view") as Node, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) pieces.push(node.textContent ?? "");
    const text = pieces.join(" ");
    expect(text).not.toContain("`");
    expect(text.match(/\b[a-z]+[A-Z][A-Za-z0-9]*\b/g) ?? []).toEqual([]);
    expect(text).not.toContain('""');
    expect(text).not.toMatch(/Library model/);
  });

  it("shows each provider set as a card linking each job to a library model, and a change is a draft saved with the rest", async () => {
    // The provider sets stood as a box of values to type per set; each set now links each job to a model picked by its name
    const calls = serveSets();
    await openSettings();
    await userEvent.click(screen.getByRole("heading", { name: "Provider sets" }));
    const pc = screen.getByRole("group", { name: "provider set PC" });
    // the older machine key is read and kept in the file, never shown or chosen
    expect(screen.queryByRole("button", { name: /^Machine of/ })).not.toBeInTheDocument();
    expect(within(pc).getByRole("button", { name: "PC: Review: small.v2" })).toBeInTheDocument();
    await userEvent.click(within(pc).getByRole("button", { name: "PC: Review: small.v2" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: /^Same as the default set/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save settings (1)" }));
    const body = calls.find((call) => call.method === "PUT")?.body as { key: string; new_value: { PC: Record<string, unknown> } };
    expect(body.key).toBe("providerSets");
    expect(body.new_value.PC.phaseModels).toBeUndefined();
    expect(body.new_value.PC.machine).toBe("the desktop");
  });

  it("lists a provider's job in each set, and offers it only the models whose provider does that job", async () => {
    // Speech to text, speaker detection and the other provider jobs are linked to a library model like a phase is; a provider
    // lists the jobs it can do, so a phase is never offered a speech-only provider's model, nor speech a chat-only one's
    const hosts = { lmstudio: { url: "http://localhost:1234/v1", kinds: ["chat"] },
      voice: { url: "http://voice:8090/v1", kinds: ["speech"] } };
    installServer({
      "GET /api/phases": PHASES, "GET /api/inbox": [], "GET /api/runs": [], "GET /api/jobs": [], "GET /api/inbox/trash": [],
      "GET /api/profiles": { profiles: [], server_profile: "" },
      "GET /api/settings": [...SET_ROWS.filter((row) => row.key !== "hosts"),
        { key: "hosts", value: hosts, kind: "json", source: "local", comment: "" },
        { key: "models.whisper.spec", path: ["models", "whisper", "spec"], value: "openai-compatible:whisper-large", kind: "text",
          source: "local", comment: "" },
        { key: "models.whisper.host", path: ["models", "whisper", "host"], value: "voice", kind: "text", source: "local", comment: "" },
        { key: "providers.speech.libraryModel", path: ["providers", "speech", "libraryModel"], value: "", kind: "text", source: "code",
          comment: "" }],
    });
    await openSettings();
    await userEvent.click(screen.getByRole("heading", { name: "Provider sets" }));
    const pc = screen.getByRole("group", { name: "provider set PC" });
    // a job the set does not change is added from its "Override a job" menu, starting with the model that can do it
    await userEvent.click(within(pc).getByRole("button", { name: "Override a job" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /^Speech to text/ }));
    await userEvent.click(within(pc).getByRole("button", { name: /^PC: Speech to text/ }));
    const offered = screen.getAllByRole("menuitemradio").map((item) => item.textContent ?? "");
    expect(offered).toHaveLength(2);
    expect(offered[0]).toMatch(/^Same as the default set/);
    expect(offered[1]).toMatch(/^whisper/);
    expect(Array.from(document.querySelectorAll(".menu-group")).map((item) => item.textContent)).toEqual(["voice"]);
    await userEvent.keyboard("{Escape}");
    await userEvent.click(within(pc).getByRole("button", { name: "PC: Review: small.v2" }));
    expect(screen.queryByRole("menuitemradio", { name: /whisper/ })).not.toBeInTheDocument();
  });

  it("copies a provider set from its card, a draft named by the person", async () => {
    const calls = serveSets();
    await openSettings();
    await userEvent.click(screen.getByRole("heading", { name: "Provider sets" }));
    const pc = screen.getByRole("group", { name: "provider set PC" });
    await userEvent.click(within(pc).getByRole("button", { name: "copy provider set PC" }));
    await userEvent.type(within(pc).getByRole("textbox", { name: "name of the copy of PC" }), "PC strict");
    await userEvent.click(within(pc).getByRole("button", { name: "Create copy" }));
    expect(screen.getByRole("group", { name: "provider set PC strict" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save settings (1)" }));
    const body = calls.find((call) => call.method === "PUT")?.body as { new_value: Record<string, unknown> };
    expect(body.new_value["PC strict"]).toEqual(body.new_value.PC);
  });

  it("calls the first entry the default settings and marks the server's own profile by its name defect", async () => {
    // The first entry read "the server's settings (profile strict)" while it showed the default settings without the
    // profile, so switching to the profile seemed to change nothing. It now reads "default settings"
    serveSettings(undefined, "strict");
    await openSettings();
    await userEvent.click(screen.getByRole("button", { name: "Edit: default settings" }));
    expect(screen.getAllByRole("menuitemradio").map((item) => item.textContent)).toEqual([
      "default settingswhat every run uses unless it names a profile", "stricta profile; the server runs on it"]);
    expect(screen.queryByText(/the server's settings/)).not.toBeInTheDocument();
    // a file of the repository meant as an example to copy is no profile, and every row has an icon on its left
    expect(screen.queryByText(/local\.example/)).not.toBeInTheDocument();
    expect(document.querySelectorAll(".setting-menu button[role='menuitemradio'] > svg.icon")).toHaveLength(2);
  });

  it("shows a model's provider and model name as two fields, and saves the spec joined from them", async () => {
    // A model's spec stood as one joined text ("lmstudio:small-model"); a person now reads and types the model's name alone
    // and picks its provider from the providers' own list
    const calls = serveSettings();
    await openSettings();
    const card = screen.getByRole("group", { name: "model small.v2" });
    const name = within(card).getByRole("textbox", { name: "Model name of small.v2" });
    expect(name).toHaveValue("small-model");
    expect(within(card).getByText("Server")).toBeInTheDocument();
    expect(within(card).queryByText("lmstudio:small-model")).not.toBeInTheDocument();
    await userEvent.clear(name);
    await userEvent.type(name, "big-model");
    await userEvent.click(within(card).getByRole("button", { name: /^Server/ }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: /other-mac/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save settings (2)" }));
    expect(calls.filter((call) => call.method === "PUT").map((call) => call.body)).toEqual([
      { key: "models.small.v2.spec", new_value: "lmstudio:big-model", profile: "", key_parts: ["models", "small.v2", "spec"] },
      { key: "models.small.v2.host", new_value: "other-mac", profile: "", key_parts: ["models", "small.v2", "host"] }]);
  });

  it("adds a model as a copy of another's set fields, a card marked unsaved until Save settings writes it", async () => {
    const calls = serveSettings();
    await openSettings();
    await userEvent.click(screen.getByRole("heading", { name: "Model servers" }));
    await userEvent.click(screen.getByRole("button", { name: "add model to no server" }));
    await userEvent.type(screen.getByRole("textbox", { name: "New model's name on no server" }), "small.v2");
    expect(screen.getByText("a model has this name")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
    await userEvent.clear(screen.getByRole("textbox", { name: "New model's name on no server" }));
    await userEvent.type(screen.getByRole("textbox", { name: "New model's name on no server" }), "other-mac");
    await userEvent.click(screen.getByRole("button", { name: "Copy of, for no server: frame" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: /^small\.v2/ }));
    await userEvent.click(screen.getByRole("button", { name: "Add" }));
    const card = screen.getByRole("group", { name: "model other-mac" });
    expect(within(card).getByText("unsaved")).toBeInTheDocument();
    expect(within(card).getByRole("textbox", { name: "Model name of other-mac" })).toHaveValue("small-model");
    await userEvent.click(screen.getByRole("button", { name: "Save settings (1)" }));
    expect(calls.filter((call) => call.method === "PUT").map((call) => call.body)).toEqual([
      { key: "models.other-mac", new_value: { spec: "lmstudio:small-model", temperature: 0.7 }, profile: "",
        key_parts: ["models", "other-mac"] }]);
  });

  it("removes a model of the owner's file after asking, and a shipped model offers no Remove", async () => {
    const calls = installServer({
      "GET /api/phases": PHASES, "GET /api/inbox": [], "GET /api/runs": [], "GET /api/jobs": [], "GET /api/inbox/trash": [],
      "GET /api/profiles": { profiles: [], server_profile: "" },
      "GET /api/settings": [...ROWS, { key: "models.frame.spec", path: ["models", "frame", "spec"], value: "x", kind: "text",
        source: "default", comment: "" }],
      "POST /api/settings/remove": () => ({ exit_code: 0, output: "removed models.small.v2" }),
    });
    await openSettings();
    await userEvent.click(screen.getByRole("heading", { name: "Model servers" }));
    const shipped = screen.getByRole("group", { name: "model frame" });
    expect(within(shipped).queryByRole("button", { name: "remove model frame" })).not.toBeInTheDocument();
    expect(within(shipped).getByText(/it can be changed, not removed/)).toBeInTheDocument();
    const own = screen.getByRole("group", { name: "model small.v2" });
    await userEvent.click(within(own).getByRole("button", { name: "remove model small.v2" }));
    await userEvent.click(within(own).getByRole("button", { name: "yes, remove model small.v2" }));
    expect(await screen.findByText("removed models.small.v2")).toBeInTheDocument();
    expect(calls.filter((call) => call.method === "POST").map((call) => call.body)).toEqual([
      { key_parts: ["models", "small.v2"], profile: "" }]);
  });

  it("keeps each change as a marked draft, and Save settings in the top bar writes them all", async () => {
    const calls = serveSettings();
    const field = await openSettings();
    // the help comes from the page's own words, never the program's comment
    expect(screen.getByText("How many times a step may be tried before a rejection blocks it.")).toBeInTheDocument();
    expect(screen.queryByText("Answers a step may give")).not.toBeInTheDocument();
    // a saved value carries no mark, whichever file set it; only an unsaved change is marked
    expect(within(field.closest(".setting-row") as HTMLElement).queryByText("changed")).not.toBeInTheDocument();
    expect(within(screen.getByRole("checkbox", { name: "Run the review" }).closest(".setting-row") as HTMLElement)
      .queryByText("changed")).not.toBeInTheDocument();
    await userEvent.clear(field);
    await userEvent.type(field, "4");
    await userEvent.click(screen.getByRole("checkbox", { name: "Run the review" }));
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
    // the menu shows plain words for each stored value, never the stored code word
    await userEvent.click(screen.getByRole("button", { name: "Board display: The frame with its text changes" }));
    const list = screen.getByRole("menu", { name: "Board display" });
    expect(within(list).getAllByRole("menuitemradio").map((option) => option.textContent)).toEqual(
      ["The changed area enlarged", "The frame with its text changes"]);
    await userEvent.click(within(list).getByRole("menuitemradio", { name: "The changed area enlarged" }));
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
    // labelled fields, never a box of JSON or a table of names: an option is added from a menu of the usual ones
    await userEvent.click(screen.getByRole("button", { name: "Add an option" }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Least likely word allowed/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save settings (2)" }));
    expect(await screen.findByText(/is refused: too small/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Save settings \(\d\)/ })).toBeInTheDocument();
    // a draft that is no JSON (typed in an older page) is named, not sent
    const broken = await saveDrafts({ "models.frame.sampling": { row: ROWS[2] as SettingRow, raw: "not json" } }, "");
    expect(broken.message).toBe("models.frame.sampling: not valid JSON");
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
    const budgets = screen.getByRole("heading", { name: "Advanced limits" }).closest("details")!;
    expect(budgets.open).toBe(false);
    await userEvent.click(screen.getByRole("heading", { name: "Advanced limits" }));
    expect(budgets.open).toBe(true);
    await userEvent.type(screen.getByRole("searchbox"), "phase 5");
    expect(screen.getByRole("heading", { name: "Review and quality" }).closest("details")!.open).toBe(true);
    expect(screen.queryByRole("heading", { name: "Advanced limits" })).not.toBeInTheDocument();
    await userEvent.clear(screen.getByRole("searchbox"));
    const field = screen.getByRole("spinbutton", { name: "Tries per step" });
    await userEvent.clear(field);
    await userEvent.type(field, "5");
    expect(screen.getByRole("button", { name: "Save settings (1)" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Edit: default settings" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: /^strict/ }));
    await userEvent.click(screen.getByRole("button", { name: "Discard the changes and switch" }));
    expect(await screen.findByText(/Profile strict sets 1 setting:/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save settings/ })).not.toBeInTheDocument();
  });

  it("names what a chosen profile sets, so a switch that changes only those rows is seen defect", async () => {
    // Choosing a profile that sets two model addresses left every row in view as it was, and the owner read it as the
    // switch changing nothing. The page now names the settings the profile sets in one line above the groups
    serveSettings();
    await openSettings();
    await userEvent.click(screen.getByRole("button", { name: "Edit: default settings" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: /^strict/ }));
    expect(await screen.findByText(/Profile strict sets 1 setting:/)).toHaveTextContent(
      "Profile strict sets 1 setting: Tries per step. Every other value is the default settings'.");
  });

  it("asks before a switch drops unsaved changes, and switches at once with none", async () => {
    serveSettings();
    await openSettings();
    const field = screen.getByRole("spinbutton", { name: "Tries per step" });
    await userEvent.clear(field);
    await userEvent.type(field, "5");
    await userEvent.click(screen.getByRole("button", { name: "Edit: default settings" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: /^strict/ }));
    expect(screen.getByRole("alert")).toHaveTextContent("You have 1 unsaved change to the default settings");
    await userEvent.click(screen.getByRole("button", { name: "Stay on the default settings" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save settings (1)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit: default settings" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Discard changes" }));
    await userEvent.click(screen.getByRole("button", { name: "Edit: default settings" }));
    await userEvent.click(screen.getByRole("menuitemradio", { name: /^strict/ }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(await screen.findByText(/Profile strict sets/)).toBeInTheDocument();
  });
});
