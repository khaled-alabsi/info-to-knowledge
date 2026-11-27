import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { PHASES, installServer, makeInboxItem, makeJob } from "../test/fakeServer";
import { SPEED_OPTIONS } from "../test/speedOptions";
import VideoDetail from "./VideoDetail";

const ITEM = makeInboxItem("Gateway.mp4", "new");

describe("VideoDetail", () => {
  it("plays a new video under its Recording tab and starts its run with the phases chosen", async () => {
    const calls = installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "POST /api/jobs": [makeJob()] });
    const onChanged = vi.fn();
    const onMessage = vi.fn();
    render(<VideoDetail item={ITEM} phases={PHASES} jobs={[]} onBack={() => {}} onOpenJob={() => {}}
      onChanged={onChanged} onMessage={onMessage} />);
    expect(screen.getByRole("heading", { name: "Gateway.mp4" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Recording" })).toHaveAttribute("aria-selected", "true");
    expect(document.querySelector("video")?.getAttribute("src")).toBe(`/api/inbox/video?path=${encodeURIComponent(ITEM.video)}`);
    expect(screen.queryByText(/Not started/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Start run/ }));
    const queued = calls.find((call) => call.method === "POST" && call.url === "/api/jobs");
    expect(queued?.body).toMatchObject({ items: [{ video: ITEM.video, output: ITEM.output }] });
    expect(onChanged).toHaveBeenCalled();
    expect(onMessage).toHaveBeenCalledWith(expect.stringContaining("Starting Gateway.mp4"));
  });

  it("says the run waits behind the jobs ahead of it, never that it starts", async () => {
    installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "POST /api/jobs": [makeJob()] });
    const onMessage = vi.fn();
    render(<VideoDetail item={ITEM} phases={PHASES} jobs={[makeJob({ job_id: "job-0", status: "running" }, { video: "/work/inbox/Other.mp4" })]}
      onBack={() => {}} onOpenJob={() => {}} onChanged={() => {}} onMessage={onMessage} />);
    await userEvent.click(screen.getByRole("button", { name: /Start run/ }));
    expect(onMessage).toHaveBeenCalledWith("Queued Gateway.mp4 behind 1 job");
  });

  const PROFILES = { profiles: [
    { name: "knowledge", aliases: [], description: "a reference document" },
    { name: "meeting", aliases: ["call"], description: "people deciding" },
    { name: "takeaway", aliases: ["facts"], description: "only the facts settled" },
  ] };

  it("asks a video's profile only once it is processed as audio, and sends both with the run", async () => {
    const calls = installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "GET /api/ingesting-profiles": PROFILES,
      "POST /api/jobs": [makeJob()] });
    render(<VideoDetail item={ITEM} phases={PHASES} jobs={[]} onBack={() => {}} onOpenJob={() => {}}
      onChanged={() => {}} onMessage={() => {}} />);
    expect(screen.queryByRole("combobox", { name: "ingesting profile" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: "Audio only" }));
    const profile = await screen.findByRole("combobox", { name: "ingesting profile" });
    await screen.findByRole("option", { name: "takeaway" });
    await userEvent.selectOptions(profile, "takeaway");
    expect(profile).toHaveValue("takeaway");
    await userEvent.click(screen.getByRole("button", { name: /Start run/ }));
    const queued = calls.find((call) => call.method === "POST" && call.url === "/api/jobs");
    expect(queued?.body).toMatchObject({ ingesting_profile: "takeaway", is_run_as_audio: true });
  });

  it("offers a PDF its processing and its document shaping, not process-as or the workflow, and sends what was chosen", async () => {
    const calls = installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "GET /api/ingesting-profiles": PROFILES,
      "POST /api/jobs": [makeJob()] });
    render(<VideoDetail item={makeInboxItem("guide.pdf", "new")} phases={PHASES} jobs={[]} onBack={() => {}}
      onOpenJob={() => {}} onChanged={() => {}} onMessage={() => {}} />);
    expect(screen.queryByRole("radiogroup", { name: "process as" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^workflow speed/ })).not.toBeInTheDocument();
    const processing = screen.getByRole("combobox", { name: "processing" });
    const shaping = screen.getByRole("combobox", { name: "document shaping" });
    await screen.findByRole("option", { name: "LLM-written document" });
    // Text offers the markdown shapings and knowledge; the profiles of people speaking are never a PDF's
    expect(within(shaping).getByRole("option", { name: "Markdown" })).toBeInTheDocument();
    for (const name of ["meeting", "takeaway"]) {
      expect(within(shaping).queryByRole("option", { name })).not.toBeInTheDocument();
    }
    await userEvent.selectOptions(processing, "pages");
    // page pictures are always written by a model: the markdown shapings leave the list
    expect(within(shaping).queryByRole("option", { name: "Markdown" })).not.toBeInTheDocument();
    await userEvent.selectOptions(shaping, "knowledge");
    await userEvent.click(screen.getByRole("button", { name: /Start run/ }));
    const queued = calls.find((call) => call.method === "POST" && call.url === "/api/jobs");
    expect(queued?.body).toMatchObject({ pdf_mode: "pages-as-images", document_pass: "" });
  });

  it("sends a standing shaping as the markdown conversion with its pass", async () => {
    const calls = installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "GET /api/ingesting-profiles": PROFILES,
      "POST /api/jobs": [makeJob()] });
    render(<VideoDetail item={makeInboxItem("guide.pdf", "new")} phases={PHASES} jobs={[]} onBack={() => {}}
      onOpenJob={() => {}} onChanged={() => {}} onMessage={() => {}} />);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "document shaping" }), "rewrite");
    expect(screen.getByRole("combobox", { name: "processing" })).toHaveValue("text");
    await userEvent.click(screen.getByRole("button", { name: /Start run/ }));
    const queued = calls.find((call) => call.method === "POST" && call.url === "/api/jobs");
    expect(queued?.body).toMatchObject({ pdf_mode: "markdown-conversion", document_pass: "rewrite", ingesting_profile: "" });
  });

  const BY_SIZE = { pdf_mode: "by-size", pdf_images_page_limit: 20, pdf_model_writing_page_limit: 300,
    pdf_ocr_engine: "unlimited-ocr", knowledge_filter_model: "google-SigLIP-2", workflow: "quality" };

  for (const [pages, processing, shaping] of [[8, "Pages as images", "LLM-written document"],
    [120, "Text", "LLM-written document"],
    [1493, "Text", "Markdown"]] as const) {
    it(`names the defaults a PDF of ${pages} pages takes by its size`, async () => {
      const calls = installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "GET /api/ingesting-profiles": PROFILES,
        "GET /api/run-defaults?profile=": BY_SIZE, "POST /api/jobs": [makeJob()] });
      render(<VideoDetail item={makeInboxItem("guide.pdf", "new", { page_count: pages })} phases={PHASES} jobs={[]} onBack={() => {}}
        onOpenJob={() => {}} onChanged={() => {}} onMessage={() => {}} />);
      expect(await screen.findByDisplayValue(`default: ${processing} (${pages} pages)`)).toBeInTheDocument();
      expect(screen.getByRole("combobox", { name: "document shaping" })).toHaveDisplayValue(`default: ${shaping}`);
      expect(screen.getByRole("combobox", { name: "scans" })).toHaveDisplayValue("default: Unlimited-OCR-2");
      // both kept default: nothing is sent, and the server picks by the same size
      await userEvent.click(screen.getByRole("button", { name: /Start run/ }));
      const queued = calls.find((call) => call.method === "POST" && call.url === "/api/jobs");
      expect(queued?.body).not.toMatchObject({ pdf_mode: expect.stringMatching(/./) });
    });
  }

  it("offers a PDF what reads its scanned pages and sends the engine chosen, no reader", async () => {
    const calls = installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "GET /api/ingesting-profiles": PROFILES,
      "POST /api/jobs": [makeJob()] });
    render(<VideoDetail item={makeInboxItem("guide.pdf", "new")} phases={PHASES} jobs={[]} onBack={() => {}}
      onOpenJob={() => {}} onChanged={() => {}} onMessage={() => {}} />);
    expect(screen.queryByRole("combobox", { name: "PDF reader" })).not.toBeInTheDocument();
    const scans = screen.getByRole("combobox", { name: "scans" });
    expect(scans).toHaveDisplayValue("default");
    expect(within(scans).getAllByRole("option").map((option) => option.textContent))
      .toEqual(["default", "Unlimited-OCR-2", "Tesseract"]);
    await userEvent.selectOptions(scans, "Unlimited-OCR-2");
    await userEvent.click(screen.getByRole("button", { name: /Start run/ }));
    const queued = calls.find((call) => call.method === "POST" && call.url === "/api/jobs");
    expect(queued?.body).toMatchObject({ pdf_ocr_engine: "unlimited-ocr" });
    expect(queued?.body).not.toHaveProperty("pdf_reader");
  });

  it("explains each PDF menu in plain words on its help button", async () => {
    installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "GET /api/ingesting-profiles": PROFILES });
    render(<VideoDetail item={makeInboxItem("guide.pdf", "new")} phases={PHASES} jobs={[]} onBack={() => {}}
      onOpenJob={() => {}} onChanged={() => {}} onMessage={() => {}} />);
    expect(screen.queryByRole("note", { name: "the PDF options explained" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "explain the PDF options" }));
    const help = screen.getByRole("note", { name: "the PDF options explained" });
    expect(help).toHaveTextContent("what reads a scanned page");
    expect(help).toHaveTextContent("Tesseract");
    expect(help).not.toHaveTextContent("MinerU");
  });

  it("offers a video its knowledge filter model and sends the model chosen", async () => {
    const calls = installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "GET /api/ingesting-profiles": PROFILES,
      "POST /api/jobs": [makeJob()] });
    render(<VideoDetail item={ITEM} phases={PHASES} jobs={[]} onBack={() => {}} onOpenJob={() => {}}
      onChanged={() => {}} onMessage={() => {}} />);
    const filter = screen.getByRole("combobox", { name: "knowledge filter model" });
    // untouched, the settings decide (google-SigLIP-2 by default)
    expect(filter).toHaveDisplayValue("default");
    await userEvent.selectOptions(filter, "liquidai-d1-2B");
    await userEvent.click(screen.getByRole("button", { name: /Start run/ }));
    const queued = calls.find((call) => call.method === "POST" && call.url === "/api/jobs");
    expect(queued?.body).toMatchObject({ knowledge_filter_model: "liquidai-d1-2B" });
  });

  it("offers no knowledge filter model for a PDF", () => {
    installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "GET /api/ingesting-profiles": PROFILES });
    render(<VideoDetail item={makeInboxItem("guide.pdf", "new")} phases={PHASES} jobs={[]} onBack={() => {}}
      onOpenJob={() => {}} onChanged={() => {}} onMessage={() => {}} />);
    expect(screen.queryByRole("combobox", { name: "knowledge filter model" })).not.toBeInTheDocument();
  });

  it("offers no PDF menu and no help for a video", () => {
    installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "GET /api/ingesting-profiles": PROFILES });
    render(<VideoDetail item={makeInboxItem("meeting.mp4", "new")} phases={PHASES} jobs={[]} onBack={() => {}}
      onOpenJob={() => {}} onChanged={() => {}} onMessage={() => {}} />);
    expect(screen.queryByRole("combobox", { name: "scans" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "explain the PDF options" })).not.toBeInTheDocument();
  });

  it("drops the profile when a video goes back to being a video", async () => {
    const calls = installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "GET /api/ingesting-profiles": PROFILES,
      "POST /api/jobs": [makeJob()] });
    render(<VideoDetail item={ITEM} phases={PHASES} jobs={[]} onBack={() => {}} onOpenJob={() => {}}
      onChanged={() => {}} onMessage={() => {}} />);
    await userEvent.click(screen.getByRole("radio", { name: "Audio only" }));
    await screen.findByRole("option", { name: "takeaway" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "ingesting profile" }), "takeaway");
    await userEvent.click(screen.getByRole("radio", { name: "Video" }));
    expect(screen.queryByRole("combobox", { name: "ingesting profile" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Start run/ }));
    const queued = calls.find((call) => call.method === "POST" && call.url === "/api/jobs");
    expect(queued?.body).toMatchObject({ ingesting_profile: "", is_run_as_audio: false });
  });

  it("offers a video the Workflow speed, sends the preset and each value changed, and drops it when the video runs as audio", async () => {
    const calls = installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "GET /api/ingesting-profiles": PROFILES,
      "GET /api/run-defaults?profile=": { ...BY_SIZE, speed: SPEED_OPTIONS }, "POST /api/jobs": [makeJob()] });
    render(<VideoDetail item={ITEM} phases={PHASES} jobs={[]} onBack={() => {}} onOpenJob={() => {}}
      onChanged={() => {}} onMessage={() => {}} />);
    await userEvent.click(await screen.findByRole("button", { name: "workflow speed: Quality" }));
    await userEvent.click(screen.getByRole("radio", { name: /^Balanced/ }));
    // a value changed from the preset makes the speed Custom
    await userEvent.click(screen.getByRole("switch", { name: /^Review/ }));
    expect(screen.getByRole("button", { name: "workflow speed: Custom" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    await userEvent.click(screen.getByRole("button", { name: /Start run/ }));
    const queued = calls.find((call) => call.method === "POST" && call.url === "/api/jobs");
    expect(queued?.body).toMatchObject({ speed_preset: "balanced", speed_settings: { "review.isOn": false },
      is_run_as_audio: false });
    await userEvent.click(screen.getByRole("radio", { name: "Audio only" }));
    expect(screen.queryByRole("button", { name: /^workflow speed/ })).not.toBeInTheDocument();
  });

  it("asks an audio file no Workflow speed", async () => {
    installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "GET /api/ingesting-profiles": PROFILES,
      "GET /api/run-defaults?profile=": { ...BY_SIZE, speed: SPEED_OPTIONS } });
    render(<VideoDetail item={makeInboxItem("Jonas-call.mp3", "new")} phases={PHASES} jobs={[]} onBack={() => {}}
      onOpenJob={() => {}} onChanged={() => {}} onMessage={() => {}} />);
    await screen.findByRole("option", { name: "takeaway" });
    expect(screen.queryByRole("button", { name: /^workflow speed/ })).not.toBeInTheDocument();
  });

  it("asks an audio file no Process as, and offers it every profile", async () => {
    const calls = installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "GET /api/ingesting-profiles": PROFILES,
      "POST /api/jobs": [makeJob()] });
    render(<VideoDetail item={makeInboxItem("Jonas-call.mp3", "new")} phases={PHASES} jobs={[]} onBack={() => {}}
      onOpenJob={() => {}} onChanged={() => {}} onMessage={() => {}} />);
    await screen.findByRole("option", { name: "takeaway" });
    expect(screen.queryByRole("radiogroup", { name: "process as" })).not.toBeInTheDocument();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "ingesting profile" }), "meeting");
    await userEvent.click(screen.getByRole("button", { name: /Start run/ }));
    const queued = calls.find((call) => call.method === "POST" && call.url === "/api/jobs");
    expect(queued?.body).toMatchObject({ ingesting_profile: "meeting", is_run_as_audio: false });
  });

  it("takes tags under the title, as a run's page does, and sends them with the run", async () => {
    const calls = installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "GET /api/tags": { tags: [], profiles: [] },
      "PUT /api/inbox/tags": { video: ITEM.video, tags: [] }, "POST /api/jobs": [makeJob()] });
    const onMessage = vi.fn();
    render(<VideoDetail item={{ ...ITEM, tags: ["lecture"] }} phases={PHASES} jobs={[]} onBack={() => {}} onOpenJob={() => {}}
      onChanged={() => {}} onMessage={onMessage} />);
    expect(within(screen.getByLabelText("tags")).getByText("lecture")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "add a tag" }));
    await userEvent.type(screen.getByRole("combobox", { name: "add a tag" }), "week 42{Enter}");
    expect(within(screen.getByLabelText("tags")).getByText("week 42")).toBeInTheDocument();
    const saved = calls.filter((call) => call.method === "PUT" && call.url === "/api/inbox/tags").at(-1);
    expect(saved?.body).toEqual({ video: ITEM.video, tags: ["lecture", "week 42"] });
    expect(onMessage).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /Start run/ }));
    const queued = calls.find((call) => call.method === "POST" && call.url === "/api/jobs");
    expect(queued?.body).toMatchObject({ tags: ["lecture", "week 42"] });
  });

  it("renames the video from its title, and follows it to its new name", async () => {
    const calls = installServer({ "GET /api/profiles": { profiles: [], server_profile: "" },
      "POST /api/inbox/rename": { video: "/work/inbox/Gateway-review.mp4", name: "Gateway-review.mp4" } });
    const onRenamed = vi.fn();
    render(<VideoDetail item={ITEM} phases={PHASES} jobs={[]} onBack={() => {}} onOpenJob={() => {}}
      onChanged={() => {}} onMessage={() => {}} onRenamed={onRenamed} />);
    await userEvent.click(screen.getByRole("button", { name: "rename" }));
    const box = screen.getByRole("textbox", { name: "new name" });
    await userEvent.clear(box);
    await userEvent.type(box, "Gateway-review{Enter}");
    expect(calls.find((call) => call.url === "/api/inbox/rename")?.body).toEqual({ video: ITEM.video, name: "Gateway-review" });
    expect(onRenamed).toHaveBeenCalledWith(expect.objectContaining({ video: "/work/inbox/Gateway-review.mp4",
      name: "Gateway-review.mp4", path: "inbox/Gateway-review.mp4" }));
  });

  it("offers the worker set in the row of a new recording's page and starts the run on the set chosen defect", async () => {
    // Defect: a new recording's page had the Worker set only behind Start run's gear, and the owner saw no way to pick
    // it. Fix: the row beside Workflow speed has it, and the run is queued on the set chosen.
    const calls = installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "POST /api/jobs": [makeJob()],
      "GET /api/worker-sets": { default_set: "default", sets: [{ name: "default", changes: [], machine: "main machine" },
        { name: "gpu-box", changes: ["models"], machine: "gpu-box" }] } });
    render(<VideoDetail item={ITEM} phases={PHASES} jobs={[]} onBack={() => {}} onOpenJob={() => {}}
      onChanged={() => {}} onMessage={() => {}} />);
    const choice = await screen.findByRole("combobox", { name: "worker set" });
    expect(within(choice).getByRole("option", { name: "default · main machine (default)" })).toBeInTheDocument();
    await userEvent.selectOptions(choice, "gpu-box");
    await userEvent.click(screen.getByRole("button", { name: /Start run/ }));
    const queued = calls.find((call) => call.method === "POST" && call.url === "/api/jobs");
    expect(queued?.body).toMatchObject({ worker_set: "gpu-box" });
  });

  it("names each choice of the run row once and keeps the rest behind Start run's gear defect", async () => {
    // Defect: the row read "Workflow settings default", "Settings default settings" and an unlabelled "default models",
    // six boxes for one action. Fix: two labelled toggle pairs, and the phases, settings and models behind the ⚙ of
    // Start run, which names any of them that is not the default.
    installServer({ "GET /api/profiles": { profiles: ["strict"], server_profile: "" } });
    render(<VideoDetail item={ITEM} phases={PHASES} jobs={[]} onBack={() => {}} onOpenJob={() => {}}
      onChanged={() => {}} onMessage={() => {}} />);
    // each name sits inside the box of its own buttons, so it never wraps away from them
    expect(screen.getByText("Process").closest(".field-toggle")).toContainElement(screen.getByRole("radiogroup", { name: "process as" }));
    expect(screen.getByRole("button", { name: "phases, settings and models" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Start run/ })).toHaveTextContent(/^Start run$/);
  });

  it("lists the phases of the flow chosen, an audio run's with its own names and no review defect", async () => {
    // Defect: with Audio only chosen, the phases still read as a video's six, review and critique collection included.
    // Fix: the page asks the server for the phases of the chosen flow (/api/phases?medium=audio) and lists those.
    const calls = installServer({ "GET /api/profiles": { profiles: [], server_profile: "" }, "GET /api/ingesting-profiles": PROFILES,
      "GET /api/phases?medium=audio&workflow=&ingesting_profile=": [
        { number: 2, name: "frames_preparing", title: "speech preparing", is_on: true },
        { number: 5, name: "review", title: "review", is_on: false }] });
    render(<VideoDetail item={ITEM} phases={PHASES} jobs={[]} onBack={() => {}} onOpenJob={() => {}}
      onChanged={() => {}} onMessage={() => {}} />);
    await userEvent.click(screen.getByRole("radio", { name: "Audio only" }));
    await vi.waitFor(() => expect(calls.some((call) => call.url.startsWith("/api/phases?medium=audio"))).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "phases, settings and models" }));
    await userEvent.click(screen.getByRole("radio", { name: "From" }));
    const phase = await screen.findByRole("radiogroup", { name: "phase" });
    expect(within(phase).getByRole("radio", { name: "2 · speech preparing" })).toBeInTheDocument();
    expect(within(phase).getByRole("radio", { name: "5 · review (off)" })).toBeDisabled();
  });

  it("chooses the settings and each phase's model behind Start run's gear and sends them with the run", async () => {
    const calls = installServer({ "GET /api/profiles": { profiles: ["strict"], server_profile: "" },
      "GET /api/phase-models": { models: [{ name: "document", spec: "lmstudio:big" }, { name: "small", spec: "lmstudio:small" }],
        phases: [{ phase: "finalize", number: 4, title: "document writing", default: "document" }] },
      "POST /api/jobs": [makeJob()] });
    render(<VideoDetail item={ITEM} phases={PHASES} jobs={[]} onBack={() => {}} onOpenJob={() => {}}
      onChanged={() => {}} onMessage={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: "phases, settings and models" }));
    await userEvent.click(document.querySelectorAll<HTMLElement>(".phase-more > summary")[1]);
    await userEvent.click(await screen.findByRole("radio", { name: "profile: strict" }));
    await userEvent.click(within(await screen.findByRole("radiogroup", { name: "model of document writing" }))
      .getByRole("radio", { name: /small/ }));
    expect(screen.getByRole("button", { name: /Start run/ })).toHaveTextContent("profile strict · models: 1 changed");
    await userEvent.click(screen.getByRole("button", { name: /Start run/ }));
    const queued = calls.find((call) => call.method === "POST" && call.url === "/api/jobs");
    expect(queued?.body).toMatchObject({ profile: "strict", phase_models: { finalize: "small" } });
  });

  it("says a video already sent to the queue is waiting and opens its job's console", async () => {
    const job = makeJob({ status: "queued" }, { video: ITEM.video });
    const onOpenJob = vi.fn();
    render(<VideoDetail item={ITEM} phases={PHASES} jobs={[job]} onBack={() => {}} onOpenJob={onOpenJob}
      onChanged={() => {}} onMessage={() => {}} />);
    expect(screen.queryByRole("button", { name: /Start run/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "queued ›" }));
    expect(onOpenJob).toHaveBeenCalledWith(job);
  });
});
