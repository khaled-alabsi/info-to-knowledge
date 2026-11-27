// The words the Settings page shows for each setting the server sends: a plain label, a one-sentence help, and the
// group of the page each setting belongs to. A setting the program reads nowhere is marked hidden.

export type SettingLabel = { label: string; help?: string; hidden?: boolean };

export type SectionGroup = { id: string; title: string; intro: string; prefixes: string[] };

// The phases as the page names them elsewhere, by the name the server sends in `phaseModels`
export const PHASE_LABELS: Record<string, string> = {
  frames_preparing: "Frames preparing",
  frames_processing: "Frames processing",
  finalize: "Document writing",
  review: "Review",
  critique_collection: "Critique collection",
  document_reshaping: "Document reshaping",
};

// What each model of the default settings is for; a model added later has no entry here
export const MODEL_PURPOSE: Record<string, string> = {
  frame: "Reads the frames",
  document: "Writes the document",
  "lfm-vl-3b": "Finds repeated frames",
  "qwen3.5-4b-filter": "Finds repeated frames, with a second small model",
  "qwen3.5-4b": "Describes frames in the fast workflow",
  default: "Serves every model that has no entry of its own",
};

// Each field of a model, by the last part of its key
export const MODEL_FIELD_LABELS: Record<string, SettingLabel> = {
  spec: { label: "Model", help: "Which model this entry runs, written as the server it comes from and the model's name." },
  host: { label: "Server", help: "Which model server this model runs on; empty uses the server named like the model's provider." },
  framesPerCall: { label: "Frames per call", help: "How many frames this model reads in one call; empty uses the general frames-per-call setting." },
  numCtx: { label: "Context window", help: "How much text the model can read in one call, which the program measures each call against when the server does not say." },
  temperature: { label: "Randomness", help: "Higher values make the answers more varied and lower values make them more repeatable." },
  topP: { label: "Word choice spread", help: "Limits the model to its most likely words; lower values keep the wording closer to the usual." },
  sampling: { label: "Other sampling options", help: "Further sampling options from the model maker's recommended set, sent only to servers that accept them." },
  numPredict: { label: "Fallback reply limit", help: "The most the model may write in one reply, thinking included, when no longest reply is set." },
  maxTokens: { label: "Longest reply", help: "The most the model may write in one reply, thinking included; empty leaves the limit to the server." },
  requestTimeoutS: { label: "Longest wait for one reply", help: "How many seconds the program waits for one answer from the server before it gives up." },
  apiKey: { label: "Access key", help: "Only for a local server that insists on a key; keep real keys out of the settings." },
  structuredMethod: { label: "Fixed-form answers", help: "How the program asks the model for answers in a fixed form; leave it empty unless a model gives broken answers." },
  reasoning: { label: "Keep thinking text", help: "Nothing reads this setting any more.", hidden: true },
};

// The model fields most people never change
export const ADVANCED_MODEL_FIELDS: string[] = [
  "numCtx", "temperature", "topP", "sampling", "numPredict", "maxTokens", "requestTimeoutS", "apiKey", "structuredMethod",
];

// The kinds of model server and what the page calls each
const PROVIDER_KINDS: Record<string, string> = {
  chat: "Scanned page reader",
  speech: "Speech to text",
  diarization: "Speaker detection",
  embeddings: "Picture comparison",
  decision: "Decision filter",
};

function buildProviderEntries(): Record<string, SettingLabel> {
  const entries: Record<string, SettingLabel> = {};
  for (const [kind, name] of Object.entries(PROVIDER_KINDS)) {
    entries[`providers.${kind}.host`] = {
      label: `${name} server`,
      help: "Which model server answers this kind of request; empty runs it on this machine the way the program always has.",
    };
    entries[`providers.${kind}.model`] = {
      label: `${name} model`,
      help: "The model's name on that server; empty uses the name the program normally uses.",
    };
    entries[`providers.${kind}.timeoutS`] = {
      label: `${name} wait`,
      help: "How many seconds one request may take before the program gives up on it.",
    };
  }
  delete entries["providers.chat.model"];
  return entries;
}

// The models of the default settings, field by field
const DEFAULT_MODEL_NAMES = ["frame", "document", "lfm-vl-3b", "qwen3.5-4b-filter", "qwen3.5-4b"];

function buildModelEntries(): Record<string, SettingLabel> {
  const entries: Record<string, SettingLabel> = {};
  for (const name of DEFAULT_MODEL_NAMES) {
    for (const [field, label] of Object.entries(MODEL_FIELD_LABELS)) entries[`models.${name}.${field}`] = label;
  }
  return entries;
}

function buildPhaseEntries(): Record<string, SettingLabel> {
  const entries: Record<string, SettingLabel> = {};
  for (const [phase, title] of Object.entries(PHASE_LABELS)) {
    entries[`phaseModels.${phase}`] = {
      label: title,
      help: "Which model this phase uses; empty uses the phase's usual model.",
    };
  }
  return entries;
}

export const SETTING_LABELS: Record<string, SettingLabel> = {
  ...buildModelEntries(),
  ...buildPhaseEntries(),
  ...buildProviderEntries(),

  // Machines and models
  hosts: { label: "Model servers", help: "Every model server by name with its address; models and kinds of model pick the server they run on by this name." },
  speedPresets: { label: "Speed presets", help: "The choices in the Workflow speed menu, each a name and the settings it sets." },
  workerSets: { label: "Worker sets", help: "Named groups of settings that replace the usual ones, so jobs on different sets can run on different machines at the same time." },
  defaultWorkerSet: { label: "Default worker set", help: "The worker set a job takes when none is chosen." },
  machine: { label: "This machine's name", help: "How this machine is called wherever a worker set is chosen." },

  // Frames
  "frames.mode": { label: "Capture mode", help: "How frames are picked from a video: at a fixed rate, at scene changes, or whenever the shared screen changes." },
  "frames.perMinute": { label: "Frames per minute", help: "For the fixed rate mode: how many frames are taken from each minute of video." },
  "frames.sceneThreshold": { label: "Scene change size", help: "For the scene mode: how different two moments must look before a new scene starts." },
  "frames.minSceneSeconds": { label: "Shortest scene", help: "For the scene mode: a scene shorter than this many seconds joins its neighbour." },
  "frames.maxCount": { label: "Most frames", help: "The most frames taken from one video, in every mode." },
  "frames.jpegQuality": { label: "Picture quality", help: "How sharp the saved frames are; higher is sharper and makes bigger files." },
  "frames.changeToCapture": { label: "Change needed for a frame", help: "For the screen change mode: the share of the screen that must change before a frame is taken." },
  "frames.stillSeconds": { label: "Settle time", help: "For the screen change mode: how many seconds the screen must stay still after a change before the frame is taken." },
  "frames.busySeconds": { label: "Longest wait for a still screen", help: "For the screen change mode: how many seconds the screen may keep moving before a frame is taken anyway." },
  "frames.boxEverySeconds": { label: "Screen search interval", help: "For the screen change mode: how often the program looks again for where the shared screen sits." },
  "frames.passThroughSeconds": { label: "Skip flicked-past screens", help: "For the screen change mode: a repeated screen shown for less than this many seconds is not taken." },
  "frames.buildStepChange": { label: "Slide build size", help: "For the screen change mode: a frame that only adds a little to the one before it, such as a box or an arrow, counts as one step of the same slide." },
  "frames.faceModel": { label: "Face finder", help: "The model that finds faces in frames." },
  "frames.personModel": { label: "Person finder", help: "The model that finds people, so a lecturer walking in front of an unchanged board does not cause a new frame." },
  "frames.trimBars": { label: "Cut away screen bars", help: "Removes menu bars, title bars and taskbars at the edges of the shared screen." },
  "frames.maxWidth": { label: "Widest frame", help: "Frames wider than this many pixels are scaled down; 0 keeps the recorded size." },
  "frames.tagsChars": { label: "Subject tag memory", help: "How many characters of earlier subject tags each frame call is shown, so it reuses them." },
  "frames.isKnowledgeFilterOn": { label: "Use the frame filter", help: "Drops frames that show only people, a gallery, or a blank or unreadable screen." },
  "frames.knowledgeClassifierCommand": { label: "Frame filter program", help: "The program that sorts each frame into people, text or other." },
  "frames.knowledgeClassifierWeights": { label: "Frame filter data", help: "The trained data the frame filter program works with." },
  "frames.knowledgeFilterModel": { label: "Frame filter model", help: "Which model decides that a frame holds no knowledge." },
  "frames.decisionFilterPython": { label: "Decision filter setup", help: "The program setup that runs the decision filter model." },
  "frames.decisionFilterCut": { label: "Decision filter cut-off", help: "Frames the decision filter scores below this chance of holding knowledge are dropped." },
  "frames.isTextOnlyMarkOn": { label: "Mark text-only frames", help: "Marks frames that show only printed text, so they are sent to the model as text instead of as a picture." },
  "frames.isBoardStageOn": { label: "Compare board frames", help: "Compares each task board frame with the board frame kept before it and drops the ones that barely changed." },
  "frames.boardHandler": { label: "Board display", help: "How the model is shown a changed board: the changed area enlarged, the whole frame with the text changes, or the text alone." },
  "frames.boardTextReader": { label: "Board text reader", help: "Which text reader reads a board's words: automatic, Apple's or Tesseract." },
  "frames.textReaderLanguages": { label: "Text reader languages", help: "The languages the text reader may recognise in frames." },
  "frames.isRedundancyFilterOn": { label: "Remove repeated frames", help: "A small vision model drops the frames that another frame already shows in full." },
  "frames.redundancyModel": { label: "Repeat-removal model", help: "The vision model that decides which frames are repeats." },
  "frames.redundancyFramesPerCall": { label: "Frames per repeat check", help: "How many consecutive frames the model looks at in one repeat check." },
  "frames.redundancyFrameWidth": { label: "Frame width for repeats", help: "Frames are scaled to this many pixels wide before the repeat check." },
  "frames.redundancyMaxMissingWords": { label: "Words allowed to go missing", help: "A removal stands only when the frames that keep its content show all but this many of the removed frame's words." },
  "frames.redundancyMinWords": { label: "Fewest words to check", help: "A removal stands only when the removed frame shows at least this many words, so photos and title cards are kept." },
  "frames.isSimilarityRecheckOn": { label: "Recheck similar frames", help: "After the repeat check, frames that look alike are compared once more by a model." },
  "frames.similarityThreshold": { label: "Similarity needed", help: "How alike two frames must look, on a scale from 0 to 1, before the model compares them." },
  "frames.similarityFramesPerCall": { label: "Frames per similarity check", help: "How many consecutive frames the model looks at in one similarity check." },
  "frames.similarityModel": { label: "Similarity model", help: "The model for the similarity recheck; empty uses the repeat-removal model." },
  "prompt.userPrompt": { label: "Your question", help: "What you want to know from the recording; it is added to the instruction every frame call gets." },
  "prompt.transcriptBeforeSeconds": { label: "Speech shown before a frame", help: "How many seconds of earlier speech go with each frame; empty sends everything spoken before it." },
  "prompt.previousFramesLimit": { label: "Earlier frames shown", help: "How many of the newest earlier frame notes go with each frame; empty sends all of them." },
  "framesProcessing.framesPerCall": { label: "Frames per call", help: "How many consecutive frames the model reads in one call; 1 means one call for each frame." },
  "framesProcessing.isCritiqueOn": { label: "Check each batch", help: "Each batch of frame notes criticises itself and records the mistakes as rules, which costs one more call per batch." },
  "framesProcessing.isCritiqueRepairOn": { label: "Repair after the check", help: "After the check, the notes are rewritten from the rules it found; off keeps the notes as first written." },
  "framesProcessing.isPdfCritiqueOn": { label: "Check PDF batches", help: "Also checks and repairs the batches of a PDF, which only matters while checking each batch is on." },
  "framesProcessing.isPdfFigureCritiqueOn": { label: "Check PDF figures", help: "Checks and repairs the batches that hold only a PDF's figures, even when checking PDF batches is off." },
  "framesProcessing.isOpenQuestionsOn": { label: "Ask open questions", help: "Each batch is asked what a reader still cannot know, then answers from its own notes, words and frames." },
  "framesProcessing.isPdfOpenQuestionsOn": { label: "Ask open questions on PDFs", help: "Also asks the open questions for the batches of a PDF." },
  "framesProcessing.repairCalls": { label: "Repair rounds", help: "How many repair rounds the frames of a batch get when the first call did not finish them all." },
  "framesProcessing.singleFrameAttempts": { label: "Single frame retries", help: "How many times one unfinished frame is sent alone before it is blocked and the phase stops; 0 blocks it at once." },
  "framesProcessing.minSentFrameWidth": { label: "Smallest frame width", help: "Frames narrower than this many pixels are enlarged before they are sent; 0 sends every frame at its own size." },
  "framesProcessing.isTextOnlyFrameSentAsText": { label: "Send text-only frames as text", help: "Frames that show only printed text go to the model as the text read from them instead of as a picture." },

  // Audio
  "audio.engine": { label: "Speech engine", help: "Which program turns speech into text." },
  "audio.whisperModel": { label: "Speech model size", help: "The size of the speech recognition model; larger is more accurate and slower." },
  "audio.language": { label: "Spoken language", help: "Leave empty to detect the language; set one only for audio the detection gets wrong." },
  "audio.fasterWhisper.device": { label: "Faster-Whisper processor", help: "Which processor the Faster-Whisper engine runs on." },
  "audio.fasterWhisper.computeType": { label: "Faster-Whisper precision", help: "How precisely the Faster-Whisper engine calculates; finer is slower." },
  "audio.fasterWhisper.beamSize": { label: "Faster-Whisper search breadth", help: "How many wordings the Faster-Whisper engine weighs for each phrase; higher can be more accurate and slower." },
  "audio.fasterWhisper.vadMinSilenceMs": { label: "Silence to skip", help: "Silences at least this many milliseconds long are skipped, which saves time on long meetings." },
  "audio.mlx.repositories": { label: "Model downloads", help: "Where each speech model size is downloaded from for the Mac speech engine." },
  "audio.mlx.chunkSeconds": { label: "Audio per call", help: "How many seconds of audio go to the graphics chip at once; values that are too long can freeze the Mac's screen." },
  "audio.mlx.noSpeechThreshold": { label: "Silence sensitivity", help: "How sure the engine must be that a stretch holds no speech before it leaves it out." },
  "audio.mlx.conditionOnPreviousText": { label: "Use earlier text as a hint", help: "Feeds the text before a stretch into the next one, which keeps the wording consistent but can carry one bad passage forward." },
  "audio.diarization.isOn": { label: "Tell speakers apart", help: "Marks which speaker said each part of the recording." },
  "audio.diarization.model": { label: "Speaker model", help: "The model that finds who speaks when." },
  "audio.diarization.device": { label: "Speaker processor", help: "Which processor finds the speakers; automatic takes the best one this machine has." },
  "audio.diarization.minSpeakers": { label: "Fewest speakers", help: "The fewest speakers the model may find; empty lets it decide." },
  "audio.diarization.maxSpeakers": { label: "Most speakers", help: "The most speakers the model may find; empty lets it decide." },
  "audio.diarization.overlapShare": { label: "Overlap limit", help: "A word gets no speaker when a second speaker covers at least this share of it, on a scale from 0 to 1." },
  "audio.takeaway.isDiarizationOn": { label: "Tell speakers apart in takeaways", help: "Whether a takeaway run separates the speakers; off is faster, since a fact needs what was agreed and not who said it." },
  "audio.takeaway.isThinkingOn": { label: "Think before takeaways", help: "Whether the model thinks before it writes a takeaway; off is faster, and on helps when facts come out wrong." },

  // Video
  "video.workflow": { label: "Video workflow", help: "How a video is processed when a run names no workflow: quality, direct draft or the earlier fast workflow." },
  "video.fastProcessing.frameModel": { label: "Frame describer", help: "The model that describes each frame in the fast workflow; a small vision model is enough." },
  "video.fastProcessing.frameFollowUps": { label: "Frames per session", help: "How many frames one description session handles before it starts fresh." },
  "video.fastProcessing.frameWidth": { label: "Frame width", help: "Frames are sent to the describer this many pixels wide." },
  "video.fastProcessing.isFrameThinkingOn": { label: "Think about frames", help: "Whether the describer thinks before it describes a frame; off makes each frame a short call." },
  "video.fastProcessing.frameMaxTokens": { label: "Longest frame description", help: "The most one frame's description may write, so a looping reply cannot run for minutes." },
  "video.fastProcessing.documentModel": { label: "Document writer", help: "The model that writes the document from all the frame descriptions in one call." },
  "video.fastProcessing.isDocumentThinkingOn": { label: "Think before writing", help: "Whether the document writer thinks before it writes." },
  "video.directDraft.frameWidth": { label: "Frame width", help: "Frames are sent to the writer this many pixels wide." },
  "video.directDraft.lastSectionChars": { label: "Earlier text shown", help: "How many characters of the draft's last section each batch is shown, so long sections do not slow every call." },
  "video.directDraft.firstLineChars": { label: "Section start shown", help: "How many characters of each section's start the writer reads beside its heading." },
  "video.directDraft.outlineCalls": { label: "Most outline calls", help: "The most calls the outline conversation may make, including every look, cut and mark on a picture." },

  // Document
  "document.chunkEntries": { label: "Notes per outline call", help: "How many consecutive notes one outline call sorts into topics." },
  "document.chunkCharacters": { label: "Characters per outline call", help: "The most characters of notes one outline call is given." },
  "document.momentSpeechChars": { label: "Speech per moment", help: "How many characters of speech are shown with each moment when the agenda is made." },
  "document.foldEntries": { label: "Short returns merged", help: "A later topic of at most this many notes whose title appeared before is written where the subject first came up; 0 merges nothing." },
  "document.sectionEntries": { label: "Notes per section call", help: "How many notes one section call writes under a single heading." },
  "document.chapterEntries": { label: "Notes per chapter part", help: "A chapter is written in parts of at most this many notes." },
  "document.headingEntries": { label: "Notes per heading", help: "A heading with more notes than this is written in even parts under the same heading." },
  "document.summaryMaxWords": { label: "Longest summary", help: "The most words the executive summary may have." },
  "document.summaryMinWords": { label: "Shortest summary", help: "The fewest words the executive summary may have, however short the document." },
  "document.minCutPixels": { label: "Smallest picture cut", help: "A cut whose shorter side is below this many pixels is flagged to the writer, who decides whether it matters." },
  "document.writeMode": { label: "Writing method", help: "Whether the document is written in one conversation of follow-ups or in separate calls for each part." },
  "document.conversationSizeLimitTokens": { label: "Conversation size limit", help: "Past this size, in tokens, the writing conversation is summarised and goes on from the summary." },
  "document.conversationWindowTokens": { label: "Fallback context window", help: "The size, in tokens, the conversation keeps within when the server does not report its own window." },
  "document.readChars": { label: "Characters per read", help: "The most characters one read of the notes returns." },
  "document.notesChars": { label: "Writer's notes size", help: "The most characters the writer's own notes may hold together." },
  "document.charsPerToken": { label: "Text length estimate", help: "How many characters the program counts as one token when it estimates sizes between the server's own counts." },
  "document.frameTokens": { label: "Frame size estimate", help: "How many tokens the program counts for one frame sent to the model when it estimates sizes." },
  "pdf.defaultMode": { label: "How PDFs are read", help: "How a PDF is read when its run chooses nothing; by size reads short ones as page pictures and long ones as converted text." },
  "pdf.imagesPageLimit": { label: "Most pages as pictures", help: "The most pages a PDF may have to be read as page pictures when the mode depends on size." },
  "pdf.modelWritingPageLimit": { label: "Most pages written by a model", help: "The most pages a PDF may have to be written by a model when the mode depends on size; a longer one is converted to text." },
  "pdf.ocrEngine": { label: "Scan reader", help: "What reads pages whose words are only pixels, such as scans." },
  "pdf.unlimitedOcrPython": { label: "Scan reader setup", help: "The program setup that runs the Unlimited-OCR scan reader." },
  "pdf.unlimitedOcrModel": { label: "Scan reader model", help: "The model the Unlimited-OCR scan reader downloads and uses." },
  "pdf.figureReading": { label: "Which figures are read", help: "Which figures of a PDF a model reads; the others are placed in the document where they stood, unread." },
  "pdf.figureReadLimit": { label: "Most figures read", help: "The most figures a model reads when figures above a limit are placed unread." },
  "pdf.cleanupWishes": { label: "Clean up wishes", help: "Extra wishes given to the clean up of a PDF's document, such as removing every ticket link." },
  "pdf.rewriteWishes": { label: "Rewrite wishes", help: "Extra wishes given to the rewrite of a PDF's document." },
  "pdf.knowledgeWishes": { label: "Knowledge wishes", help: "Extra wishes given to the knowledge pass over a PDF's document." },
  "tags.nameRules": { label: "Tag naming rules", help: "Rules that turn a recording's name pattern into a tag, applied to every run." },
  "tags.workerTags": { label: "Tags added at the end", help: "The most tags the last step of document writing adds, each reused from earlier runs or new when none fits." },
  "documentReshaping.batchTokens": { label: "Part size", help: "The least a part holds, in tokens; a part ends at the first heading past it, so a section is never cut." },
  "documentReshaping.batchCalls": { label: "Calls per part", help: "The most model calls one part may make before it is blocked." },
  "documentReshaping.conversationSizeLimitTokens": { label: "Conversation size limit", help: "Past this size, in tokens, a part's conversation is summarised and goes on from the summary." },
  "documentReshaping.firstWarningPercent": { label: "First fullness warning", help: "The share of the size limit at which the worker is told its conversation is filling up." },
  "documentReshaping.secondWarningPercent": { label: "Second fullness warning", help: "The share of the size limit at which the worker is warned again and may drop results it no longer needs." },
  "documentReshaping.charsPerToken": { label: "Text length estimate", help: "How many characters the program counts as one token when it sizes parts and warnings." },

  // Review and quality
  "review.isOn": { label: "Run the review", help: "Whether the review step runs; off leaves the document as the writing step finished it." },
  "review.maxTodos": { label: "Most review points", help: "The most gaps the review may list; a short list of real gaps beats a long one of small ones." },
  "review.reads": { label: "Reads per point", help: "How many reads the follow-up for one review point may make." },
  "review.calls": { label: "Calls per point", help: "How many tool calls the follow-up for one review point may make, reads and submits together." },
  "critiqueCollection.isOn": { label: "Collect critiques", help: "Whether the critique collection step runs; off leaves each batch's critique where it is." },

  // Advanced limits
  "budgets.maxAttempts": { label: "Tries per step", help: "How many times a step may be tried before a rejection blocks it." },
  "budgets.askWhyAfterRejection": { label: "Ask why after a rejection", help: "Before a step stops on its last rejected try, the worker gets one turn to say whether the instruction or its own answer was at fault." },
  "budgets.chapterDryRuns": { label: "Check chapters first", help: "Lets the writer check a chapter before submitting it, which gives it a second chance at its one attempt." },
  "budgets.runSteps": { label: "Most steps in a run", help: "Stops a run that keeps looping without moving forward; long recordings need a high number." },
  "budgets.writeCalls": { label: "Calls per writing follow-up", help: "The most reads and submits one follow-up of the document writing may make." },
  "budgets.writeHeadingsCalls": { label: "Calls per heading draft", help: "The most tool calls the draft of one chapter's headings may make." },
  "budgets.writeReads": { label: "Reads while writing", help: "The most reads the whole document writing may make." },
  "budgets.writeChapterCalls": { label: "Calls per chapter", help: "The most reads, looks at frames, cuts and submits one chapter may use." },
  "budgets.writeChapterAttempts": { label: "Tries per chapter", help: "How many times a chapter's text may be tried before it is rejected." },
  "budgets.cutsPerFrame": { label: "Cuts per frame", help: "The most picture cuts that may be in use on one frame." },
  "budgets.undosPerFrame": { label: "Undos per frame", help: "How often a cut on one frame may be set aside." },
  "budgets.gridLinesMax": { label: "Most grid lines", help: "The most grid lines drawn across each side of a frame or a cut when the writer looks at it." },
  "budgets.failOnFrameCutFail": { label: "Stop on a refused cut", help: "When on, a refused frame tool stops the chapter and the run so the cause is found; when off, the writer is told and goes on." },
  "budgets.attemptTimeoutS": { label: "Time per try", help: "Nothing reads this setting any more.", hidden: true },
  "budgets.modelCalls": { label: "Model calls per step", help: "Nothing reads this setting any more.", hidden: true },
  "budgets.toolCalls": { label: "Tool calls per step", help: "Nothing reads this setting any more.", hidden: true },
  "budgets.recursionLimit": { label: "Step depth", help: "Nothing reads this setting any more.", hidden: true },
  "budgets.repeatLimit": { label: "Repeat limit", help: "Nothing reads this setting any more.", hidden: true },
  "budgets.restoreOnReject": { label: "Restore on rejection", help: "Nothing reads this setting any more.", hidden: true },
  "budgets.maxReadChars": { label: "Characters per read", help: "Nothing reads this setting any more.", hidden: true },
  "budgets.maxGrepMatches": { label: "Most search matches", help: "Nothing reads this setting any more.", hidden: true },
  "queue.retries": { label: "Retries", help: "How many times a job that ended incomplete or crashed is run again." },
  "queue.retryPauseSeconds": { label: "Pause before a retry", help: "How many seconds a failed job waits before it runs again, so a restarting model server has time to come back." },
  "queue.onFailure": { label: "After a failed job", help: "What happens after a job's last try: continue starts the next job, block holds the queue until you retry or skip the job." },
};

// The page's sections, by the person's job; each top-level key prefix of the settings is in exactly one group
export const SECTION_GROUPS: SectionGroup[] = [
  {
    id: "machines-and-models",
    title: "Machines and models",
    intro: "Which machines run the work, which models they run, and which model each phase uses.",
    prefixes: ["machine", "hosts", "workerSets", "defaultWorkerSet", "models", "phaseModels", "providers"],
  },
  {
    id: "frames",
    title: "Frames",
    intro: "How frames are taken from a video, filtered, and read by the model.",
    prefixes: ["frames", "framesProcessing", "prompt"],
  },
  {
    id: "audio",
    title: "Audio",
    intro: "How speech is turned into text and how speakers are told apart.",
    prefixes: ["audio"],
  },
  {
    id: "video",
    title: "Video",
    intro: "Which workflow a video takes and what the speed menu offers.",
    prefixes: ["video", "speedPresets"],
  },
  {
    id: "document",
    title: "Document",
    intro: "How the document is written, how PDFs are read, and how tags and the final reshaping work.",
    prefixes: ["document", "pdf", "tags", "documentReshaping"],
  },
  {
    id: "review-and-quality",
    title: "Review and quality",
    intro: "The steps that check the finished document and collect what the checks found.",
    prefixes: ["review", "critiqueCollection"],
  },
  {
    id: "advanced-limits",
    title: "Advanced limits",
    intro: "Limits on tries, calls and steps, and what the queue does when a job fails.",
    prefixes: ["budgets", "queue"],
  },
];

// The first part of a key: "frames.perMinute" gives "frames"
export function readSettingPrefix(key: string): string {
  const dot = key.indexOf(".");
  return dot < 0 ? key : key.slice(0, dot);
}

// The group of the page that holds a key, or undefined for a prefix no group lists
export function findSectionGroup(key: string): SectionGroup | undefined {
  const prefix = readSettingPrefix(key);
  return SECTION_GROUPS.find((group) => group.prefixes.includes(prefix));
}

// A model key is "models.<name>.<field>" and a name may hold dots ("qwen3.5-4b"), so the field is the last part
export function splitModelKey(key: string): { name: string; field: string } | undefined {
  if (!key.startsWith("models.")) return undefined;
  const last = key.lastIndexOf(".");
  if (last <= "models.".length) return undefined;
  return { name: key.slice("models.".length, last), field: key.slice(last + 1) };
}

export function isAdvancedModelField(field: string): boolean {
  return ADVANCED_MODEL_FIELDS.includes(field);
}

function findSettingEntry(key: string): SettingLabel | undefined {
  if (key in SETTING_LABELS) return SETTING_LABELS[key];
  const model = splitModelKey(key);
  if (model && model.field in MODEL_FIELD_LABELS) return MODEL_FIELD_LABELS[model.field];
  const phase = key.startsWith("phaseModels.") ? key.slice("phaseModels.".length) : "";
  if (phase in PHASE_LABELS) return SETTING_LABELS[`phaseModels.${phase}`];
  return undefined;
}

// A key said in everyday words when no label exists: "frames.perMinute" gives "Per minute"
export function readableKey(key: string): string {
  const last = key.slice(key.lastIndexOf(".") + 1);
  const words = last.replace(/_/g, " ").replace(/([a-z0-9])([A-Z])/g, "$1 $2").toLowerCase().trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function readSettingLabel(key: string): string {
  return findSettingEntry(key)?.label ?? readableKey(key);
}

// The help of a setting, or an empty text when none is written
export function readSettingHelp(key: string): string {
  return findSettingEntry(key)?.help ?? "";
}

export function isSettingHidden(key: string): boolean {
  return findSettingEntry(key)?.hidden === true;
}
