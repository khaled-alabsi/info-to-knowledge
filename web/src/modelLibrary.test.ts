import { describe, expect, it } from "vitest";
import type { SettingRow } from "./api";
import { canModelDo, describeModelReplies, describeModelRun, describeModelUses, isModelRemovable, listJobs, listModelNames, listModelUsers,
  listModelUses, readDefaultJobModel, readModelProvider, readProviderKinds, readSetJobModel } from "./modelLibrary";

function row(key: string, value: unknown, source = "code", extra: Partial<SettingRow> = {}): SettingRow {
  return { key, path: key.split("."), value, kind: typeof value === "number" ? "number" : "text", source, comment: "", ...extra };
}

const ROWS: SettingRow[] = [
  row("hosts", { lmstudio: { url: "http://localhost:1234/v1" }, "gpu-box": { url: "http://gpu-box.local:8090/v1" } }, "local", { kind: "json" }),
  row("models.frame.spec", "lmstudio:big", "default"),
  row("models.frame.numCtx", 131072, "default"),
  row("models.frame-gpu.spec", "lmstudio:gpu-model", "local"),
  row("models.frame-gpu.host", "gpu-box", "local"),
  row("models.mixed.spec", "lmstudio:m", "default"),
  row("models.mixed.host", "gpu-box", "local"),
  row("phaseModels.frames_processing", "", "code", { resolved: "frame" }),
  row("phaseModels.review", "frame-gpu", "local"),
  row("video.fastProcessing.frameModel", "frame", "default"),
];

describe("model library", () => {
  it("lists the models by name, each once", () => {
    expect(listModelNames(ROWS)).toEqual(["frame", "frame-gpu", "mixed"]);
  });

  it("removes only a model the owner's file added, never one the shipped settings hold", () => {
    expect(isModelRemovable(ROWS, "frame-gpu")).toBe(true);
    expect(isModelRemovable(ROWS, "frame")).toBe(false);
    expect(isModelRemovable(ROWS, "mixed")).toBe(false);
    expect(isModelRemovable(ROWS, "missing")).toBe(false);
  });

  it("says where a model runs and how much it reads, in everyday words", () => {
    expect(describeModelRun(ROWS, "frame")).toEqual({ host: "lmstudio", model: "big", context: "131,072 tokens" });
    expect(describeModelRun(ROWS, "frame-gpu")).toEqual({ host: "gpu-box", model: "gpu-model", context: "automatic" });
    expect(describeModelRun(ROWS, "new", { spec: "openai:gpt" }).host).toBe("OpenAI's own service");
  });

  it("shows the longest reply and the presence penalty that tell two models on one server apart", () => {
    const rows = [row("models.a.maxTokens", 40960), row("models.a.sampling", { presence_penalty: 1 }, "code", { kind: "json" }),
      row("models.b.maxTokens", 32768), row("models.b.sampling", { presence_penalty: 0 }, "code", { kind: "json" }), row("models.c.spec", "x:y")];
    expect(describeModelReplies(rows, "a")).toBe("longest reply 40,960 tokens · presence penalty 1.0");
    expect(describeModelReplies(rows, "b")).toBe("longest reply 32,768 tokens · presence penalty 0.0");
    expect(describeModelReplies(rows, "c")).toBe("");
  });

  it("lists each phase that calls a model as a job, and the fast workflow's models as none: it uses the set's", () => {
    const jobs = listJobs(ROWS);
    expect(jobs.map((job) => job.key)).toEqual(["phaseModels.frames_processing", "phaseModels.review"]);
    expect(jobs.map((job) => job.title)).toEqual(["Frames processing", "Review"]);
  });

  it("offers a phase only the models that suit its kind of work, a model naming none suiting every phase", () => {
    const rows = [
      row("hosts", { lmstudio: { url: "http://l/v1", kinds: ["chat"] } }, "local", { kind: "json" }),
      row("models.reader.spec", "lmstudio:a"), row("models.reader.suits", ["frames"], "code", { kind: "json" }),
      row("models.small.spec", "lmstudio:b"), row("models.small.suits", ["filtering"], "code", { kind: "json" }),
      row("models.writer.spec", "lmstudio:c"), row("models.writer.suits", ["writing"], "code", { kind: "json" }),
      row("models.any.spec", "lmstudio:d"),
      row("phaseModels.frames_preparing", ""), row("phaseModels.frames_processing", ""), row("phaseModels.finalize", ""),
    ];
    const names = listModelNames(rows);
    const [preparing, processing, writing] = listJobs(rows);
    expect(names.filter((name) => canModelDo(rows, name, preparing))).toEqual(["small", "writer", "any"]);
    expect(names.filter((name) => canModelDo(rows, name, processing))).toEqual(["reader", "any"]);
    expect(names.filter((name) => canModelDo(rows, name, writing))).toEqual(["writer", "any"]);
  });

  it("lists the jobs a provider does after the phases, and offers a job only the models whose provider lists its kind", () => {
    const rows = [
      row("hosts", { lmstudio: { url: "http://l/v1", kinds: ["chat"] }, voice: { url: "http://v/v1", kinds: ["speech", "chat"] },
        old: { url: "http://o/v1" } }, "local", { kind: "json" }),
      row("models.frame.spec", "lmstudio:big"), row("models.whisper.spec", "openai-compatible:whisper"), row("models.whisper.host", "voice"),
      row("models.legacy.spec", "openai-compatible:x"), row("models.legacy.host", "old"),
      row("models.cloud.spec", "openai:gpt"),
      row("phaseModels.review", ""), row("providers.speech.libraryModel", ""), row("providers.chat.libraryModel", ""),
    ];
    const jobs = listJobs(rows);
    expect(jobs.map((job) => [job.title, job.kind])).toEqual([
      ["Review", "chat"], ["Speech to text", "speech"], ["Scanned page reader", "chat"]]);
    const names = listModelNames(rows);
    const [review, speech, reader] = jobs;
    expect(names.filter((name) => canModelDo(rows, name, speech))).toEqual(["whisper"]);
    expect(names.filter((name) => canModelDo(rows, name, review))).toEqual(["frame", "whisper", "legacy", "cloud"]);
    expect(names.filter((name) => canModelDo(rows, name, reader))).toEqual(["frame", "whisper", "legacy", "cloud"]);
    expect(readProviderKinds(rows, "old")).toEqual(["chat"]);
    expect(readModelProvider(rows, "frame")).toBe("lmstudio");
    expect(readModelProvider(rows, "cloud")).toBe("");
  });

  it("reads the model a job calls: the row's own, else the phase's usual one; a set's link, else nothing", () => {
    const [frames, review] = listJobs(ROWS);
    expect(readDefaultJobModel(frames)).toBe("frame");
    expect(readDefaultJobModel(review)).toBe("frame-gpu");
    expect(readSetJobModel({ phaseModels: { review: "mixed" } }, review)).toBe("mixed");
    expect(readSetJobModel({ phaseModels: { review: "mixed" } }, frames)).toBe("");
    expect(readSetJobModel({}, review)).toBe("");
  });

  it("names the worker sets that call a model, the default set first", () => {
    const sets = { Office: { phaseModels: { review: "mixed" } }, Other: {} };
    expect(listModelUsers(ROWS, sets, "frame")).toEqual(["default"]);
    expect(listModelUsers(ROWS, sets, "mixed")).toEqual(["Office"]);
    expect(listModelUsers(ROWS, sets, "frame-gpu")).toEqual(["default"]);
  });

  it("says for each worker set which jobs call a model", () => {
    const sets = { Office: { phaseModels: { review: "frame", frames_processing: "frame" } } };
    expect(describeModelUses(listModelUses(ROWS, sets, "frame"))).toBe(
      "default (Frames processing), Office (Frames processing, Review)");
    expect(describeModelUses(listModelUses(ROWS, sets, "nobody"))).toBe("");
  });
});
