import { describe, expect, it } from "vitest";
import {
  ADVANCED_MODEL_FIELDS, MODEL_FIELD_LABELS, MODEL_PURPOSE, PHASE_LABELS, SECTION_GROUPS, SETTING_LABELS,
  findSectionGroup, isAdvancedModelField, isSettingHidden, readSettingHelp, readSettingLabel, readSettingPrefix,
  readableKey, splitModelKey,
} from "./settingsLabels";

// Every key the server sent in its settings list, copied here so the test fetches nothing
const MODEL_NAMES = ["frame", "document", "lfm-vl-3b", "qwen3.5-4b-filter", "qwen3.5-4b"];
const MODEL_FIELDS = ["spec", "temperature", "numCtx", "numPredict", "maxTokens", "topP", "sampling", "reasoning",
  "requestTimeoutS", "host", "apiKey", "structuredMethod", "framesPerCall"];
const MODEL_KEYS = MODEL_NAMES.flatMap((name) => MODEL_FIELDS.map((field) => `models.${name}.${field}`));
const OTHER_KEYS = [
  "hosts", "phaseModels.frames_preparing", "phaseModels.frames_processing", "phaseModels.finalize",
  "phaseModels.review", "phaseModels.critique_collection", "phaseModels.document_reshaping",
  "budgets.maxAttempts", "budgets.askWhyAfterRejection", "budgets.chapterDryRuns", "budgets.runSteps",
  "budgets.attemptTimeoutS", "budgets.modelCalls", "budgets.toolCalls", "budgets.recursionLimit",
  "budgets.repeatLimit", "budgets.restoreOnReject", "budgets.maxReadChars", "budgets.maxGrepMatches",
  "budgets.writeCalls", "budgets.writeHeadingsCalls", "budgets.writeReads", "budgets.writeChapterCalls",
  "budgets.writeChapterAttempts", "budgets.cutsPerFrame", "budgets.undosPerFrame", "budgets.gridLinesMax",
  "budgets.failOnFrameCutFail",
  "frames.mode", "frames.perMinute", "frames.sceneThreshold", "frames.minSceneSeconds", "frames.maxCount",
  "frames.jpegQuality", "frames.changeToCapture", "frames.stillSeconds", "frames.busySeconds",
  "frames.boxEverySeconds", "frames.passThroughSeconds", "frames.buildStepChange", "frames.faceModel",
  "frames.personModel", "frames.trimBars", "frames.maxWidth", "frames.tagsChars", "frames.isKnowledgeFilterOn",
  "frames.knowledgeClassifierCommand", "frames.knowledgeClassifierWeights", "frames.knowledgeFilterModel",
  "frames.decisionFilterPython", "frames.decisionFilterCut", "frames.isTextOnlyMarkOn", "frames.isBoardStageOn",
  "frames.boardHandler", "frames.boardTextReader", "frames.textReaderLanguages", "frames.isRedundancyFilterOn",
  "frames.redundancyModel", "frames.redundancyFramesPerCall", "frames.redundancyFrameWidth",
  "frames.redundancyMaxMissingWords", "frames.redundancyMinWords", "frames.isSimilarityRecheckOn",
  "frames.similarityThreshold", "frames.similarityFramesPerCall", "frames.similarityModel",
  "audio.engine", "audio.whisperModel", "audio.language", "audio.fasterWhisper.device",
  "audio.fasterWhisper.computeType", "audio.fasterWhisper.beamSize", "audio.fasterWhisper.vadMinSilenceMs",
  "audio.mlx.repositories", "audio.mlx.chunkSeconds", "audio.mlx.noSpeechThreshold",
  "audio.mlx.conditionOnPreviousText", "audio.diarization.isOn", "audio.diarization.model",
  "audio.diarization.device", "audio.diarization.minSpeakers", "audio.diarization.maxSpeakers",
  "audio.diarization.overlapShare", "audio.takeaway.isDiarizationOn", "audio.takeaway.isThinkingOn",
  "video.workflow", "video.fastProcessing.frameModel", "video.fastProcessing.frameFollowUps",
  "video.fastProcessing.frameWidth", "video.fastProcessing.isFrameThinkingOn",
  "video.fastProcessing.frameMaxTokens", "video.fastProcessing.documentModel",
  "video.fastProcessing.isDocumentThinkingOn", "video.directDraft.frameWidth", "video.directDraft.lastSectionChars",
  "video.directDraft.firstLineChars", "video.directDraft.outlineCalls",
  "prompt.userPrompt", "prompt.transcriptBeforeSeconds", "prompt.previousFramesLimit",
  "framesProcessing.framesPerCall", "framesProcessing.isCritiqueOn", "framesProcessing.isCritiqueRepairOn",
  "framesProcessing.isPdfCritiqueOn", "framesProcessing.isPdfFigureCritiqueOn", "framesProcessing.isOpenQuestionsOn",
  "framesProcessing.isPdfOpenQuestionsOn", "framesProcessing.repairCalls", "framesProcessing.singleFrameAttempts",
  "framesProcessing.minSentFrameWidth", "framesProcessing.isTextOnlyFrameSentAsText",
  "document.chunkEntries", "document.chunkCharacters", "document.momentSpeechChars", "document.foldEntries",
  "document.sectionEntries", "document.chapterEntries", "document.headingEntries", "document.summaryMaxWords",
  "document.summaryMinWords", "document.minCutPixels", "document.writeMode",
  "document.conversationSizeLimitTokens", "document.conversationWindowTokens", "document.readChars",
  "document.notesChars", "document.charsPerToken", "document.frameTokens",
  "review.isOn", "review.maxTodos", "review.reads", "review.calls", "critiqueCollection.isOn",
  "documentReshaping.batchTokens", "documentReshaping.batchCalls", "documentReshaping.conversationSizeLimitTokens",
  "documentReshaping.firstWarningPercent", "documentReshaping.secondWarningPercent",
  "documentReshaping.charsPerToken",
  "pdf.defaultMode", "pdf.imagesPageLimit", "pdf.modelWritingPageLimit", "pdf.ocrEngine", "pdf.unlimitedOcrPython",
  "pdf.unlimitedOcrModel", "pdf.figureReading", "pdf.figureReadLimit", "pdf.cleanupWishes", "pdf.rewriteWishes",
  "pdf.knowledgeWishes",
  "tags.nameRules", "tags.workerTags", "queue.retries", "queue.retryPauseSeconds", "queue.onFailure",
  "providers.chat.host", "providers.chat.timeoutS", "providers.speech.host", "providers.speech.model",
  "providers.speech.timeoutS", "providers.diarization.host", "providers.diarization.model",
  "providers.diarization.timeoutS", "providers.embeddings.host", "providers.embeddings.model",
  "providers.embeddings.timeoutS", "providers.decision.host", "providers.decision.model",
  "providers.decision.timeoutS",
  "speedPresets", "workerSets", "defaultWorkerSet", "machine",
];
const ALL_KEYS = [...OTHER_KEYS, ...MODEL_KEYS];

// What a person must never read in a label or a help: a code mark, a word joined by an underscore, a dotted key, a path
const UNDERSCORE_WORD = /[A-Za-z0-9]_[A-Za-z0-9]/;
const DOTTED_KEY = /[A-Za-z0-9]\.[A-Za-z0-9]/;
const PATH = /[\\/]|\.(?:md|json|py|ts|tsx|txt|sh|yaml|yml)\b/;
const HIDDEN_KEYS = [
  "budgets.attemptTimeoutS", "budgets.modelCalls", "budgets.toolCalls", "budgets.recursionLimit",
  "budgets.repeatLimit", "budgets.restoreOnReject", "budgets.maxReadChars", "budgets.maxGrepMatches",
  ...MODEL_NAMES.map((name) => `models.${name}.reasoning`),
];

describe("the settings fixture", () => {
  it("holds every key once", () => {
    expect(ALL_KEYS).toHaveLength(238);
    expect(new Set(ALL_KEYS).size).toBe(ALL_KEYS.length);
  });
});

describe("a label for every setting", () => {
  it.each(ALL_KEYS)("%s has a label and a help", (key) => {
    const entry = SETTING_LABELS[key];
    expect(entry, `no label for ${key}`).toBeDefined();
    expect(entry.label.trim().length).toBeGreaterThan(0);
    expect(entry.label.split(/\s+/).length).toBeLessThanOrEqual(6);
    expect(entry.help ?? "").not.toBe("");
    expect(readSettingLabel(key)).toBe(entry.label);
    expect(readSettingHelp(key)).toBe(entry.help);
  });

  it.each(ALL_KEYS)("%s shows nothing a developer would read", (key) => {
    const entry = SETTING_LABELS[key];
    for (const text of [entry.label, entry.help ?? ""]) {
      expect(text, `${key}: backtick`).not.toContain("`");
      expect(text, `${key}: underscore word in "${text}"`).not.toMatch(UNDERSCORE_WORD);
      expect(text, `${key}: dotted key in "${text}"`).not.toMatch(DOTTED_KEY);
      expect(text, `${key}: path in "${text}"`).not.toMatch(PATH);
    }
  });

  it.each(ALL_KEYS)("%s has a one-sentence help", (key) => {
    const help = SETTING_LABELS[key].help ?? "";
    expect(help).toMatch(/^[A-Z].*\.$/);
    expect(help, "a second sentence").not.toMatch(/[.!?]\s+[A-Z]/);
  });

  it("marks exactly the settings the program reads nowhere as hidden", () => {
    const hidden = ALL_KEYS.filter((key) => isSettingHidden(key));
    expect([...hidden].sort()).toEqual([...HIDDEN_KEYS].sort());
  });

  it("labels every model field and marks the advanced ones", () => {
    expect(Object.keys(MODEL_FIELD_LABELS).sort()).toEqual([...MODEL_FIELDS].sort());
    for (const field of ADVANCED_MODEL_FIELDS) expect(MODEL_FIELDS).toContain(field);
    expect(isAdvancedModelField("temperature")).toBe(true);
    expect(isAdvancedModelField("spec")).toBe(false);
  });

  it("says what each model of the default settings is for", () => {
    for (const name of MODEL_NAMES) expect(MODEL_PURPOSE[name], name).toBeTruthy();
  });

  it("names the phases as the page does elsewhere", () => {
    expect(PHASE_LABELS).toEqual({
      frames_preparing: "Frames preparing", frames_processing: "Frames processing", finalize: "Document writing",
      review: "Review", critique_collection: "Critique collection", document_reshaping: "Document reshaping",
    });
    expect(readSettingLabel("phaseModels.finalize")).toBe("Document writing");
  });
});

describe("the section groups", () => {
  const prefixes = Array.from(new Set(ALL_KEYS.map(readSettingPrefix)));

  it("puts every prefix the server sends in exactly one group", () => {
    for (const prefix of prefixes) {
      const holders = SECTION_GROUPS.filter((group) => group.prefixes.includes(prefix));
      expect(holders.map((group) => group.title), `prefix ${prefix}`).toHaveLength(1);
    }
  });

  it("lists no prefix the server does not send, and none twice", () => {
    const listed = SECTION_GROUPS.flatMap((group) => group.prefixes);
    expect(new Set(listed).size).toBe(listed.length);
    for (const prefix of listed) expect(prefixes, `prefix ${prefix}`).toContain(prefix);
  });

  it("keeps the groups in the order of the person's job, each with a one-sentence intro", () => {
    expect(SECTION_GROUPS.map((group) => group.title)).toEqual([
      "Machines and models", "Frames", "Audio", "Video", "Document", "Review and quality", "Advanced limits",
    ]);
    for (const group of SECTION_GROUPS) {
      expect(group.intro).toMatch(/^[A-Z].*\.$/);
      expect(group.intro).not.toMatch(UNDERSCORE_WORD);
      expect(group.intro).not.toContain("`");
    }
  });

  it("finds the group of a key", () => {
    expect(findSectionGroup("models.qwen3.5-4b.numCtx")?.title).toBe("Machines and models");
    expect(findSectionGroup("budgets.maxAttempts")?.title).toBe("Advanced limits");
    expect(findSectionGroup("unknownPrefix.x")).toBeUndefined();
  });
});

describe("a key with no label", () => {
  it("reads as plain words", () => {
    expect(readableKey("frames.perMinute")).toBe("Per minute");
    expect(readableKey("frames_preparing")).toBe("Frames preparing");
    expect(readSettingLabel("somethingNew.maxRetryCount")).toBe("Max retry count");
    expect(readSettingHelp("somethingNew.maxRetryCount")).toBe("");
  });

  it("takes a model added later by its field", () => {
    expect(splitModelKey("models.qwen3.5-4b.numCtx")).toEqual({ name: "qwen3.5-4b", field: "numCtx" });
    expect(readSettingLabel("models.my-new-model.numCtx")).toBe("Context window");
    expect(isSettingHidden("models.my-new-model.reasoning")).toBe(true);
  });
});
