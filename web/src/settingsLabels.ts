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

// Each field of a model, by the last part of its key
export const MODEL_FIELD_LABELS: Record<string, SettingLabel> = {
  spec: { label: "Model name", help: "The model's name on its server." },
  host: { label: "Server", help: "Which model server this model runs on." },
  suits: { label: "Jobs it suits", help: "The kinds of work a provider set offers this model for; with none ticked it is offered for every job." },
  framesPerCall: { label: "Frames per call on this model", help: "How many frames this model reads in one call; empty uses the general setting." },
  numCtx: { label: "Context window (tokens)", help: "How much text the model can read in one call." },
  temperature: { label: "Randomness", help: "Higher values make the answers more varied and lower values make them more repeatable." },
  topP: { label: "Word choice spread", help: "Limits the model to its most likely words; lower values keep the wording closer to the usual." },
  sampling: { label: "More model options", help: "Further options from the model maker's recommended set, sent only to servers that accept them." },
  numPredict: { label: "Reply limit if none set (tokens)", help: "The most the model may write in one reply, thinking included, when no longest reply is set." },
  maxTokens: { label: "Longest reply (tokens)", help: "The most the model may write in one reply, thinking included; empty leaves the limit to the server." },
  requestTimeoutS: { label: "Longest wait for one reply (seconds)", help: "How long the program waits for one answer before it gives up." },
  apiKey: { label: "Login key", help: "Only for a local server that insists on a key; keep real keys out of the settings." },
  structuredMethod: { label: "Answer format", help: "How the program asks the model for answers in a fixed form; leave it empty unless a model gives broken answers." },
  reasoning: { label: "Keep thinking text", help: "Nothing reads this setting any more.", hidden: true },
};

// The model fields most people never change
export const ADVANCED_MODEL_FIELDS: string[] = [
  "numCtx", "temperature", "topP", "sampling", "numPredict", "maxTokens", "requestTimeoutS", "apiKey", "structuredMethod",
];

// The jobs a provider can do (the `kinds` of a provider) and what the page calls each: each is a job of a provider set, linked
// to a model of the library like a phase is
export const PROVIDER_KINDS: Record<string, string> = {
  chat: "Scanned page reader",
  speech: "Speech to text",
  diarization: "Speaker detection",
  embeddings: "Frame vectors",
  decision: "Decision filter",
};

// The jobs a model server can do, as tick boxes show them; a server doing chat runs the models that write and read frames
export const PROVIDER_JOB_CHOICES: { value: string; label: string }[] = Object.entries(PROVIDER_KINDS)
  .map(([value, label]) => ({ value, label: value === "chat" ? "Language and picture models" : label }));

function buildProviderEntries(): Record<string, SettingLabel> {
  const entries: Record<string, SettingLabel> = {};
  for (const [kind, name] of Object.entries(PROVIDER_KINDS)) {
    entries[`providers.${kind}.libraryModel`] = {
      label: name,
      help: "Which model does this job, as set on its model server; empty runs it on this computer the way the program always has.",
    };
    // the library model holds the provider, the model's name and the wait time; these three stay in the files for the command line
    for (const [field, word] of [["host", "provider"], ["model", "model"], ["timeoutS", "wait"]]) {
      entries[`providers.${kind}.${field}`] = { label: `${name} ${word}`, help: "Set in the model this job is linked to.", hidden: true };
    }
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
      help: "Which model this phase uses; empty uses the phase's own model, named in the menu with the provider it runs on.",
    };
  }
  return entries;
}

export const SETTING_LABELS: Record<string, SettingLabel> = {
  ...buildModelEntries(),
  ...buildPhaseEntries(),
  ...buildProviderEntries(),

  // Machines and models
  hosts: { label: "Model servers", help: "Every model server by name with its address, the name of the variable holding its access token and the jobs it can do; a model picks the server it runs on by this name." },
  speedPresets: { label: "Speed presets", help: "The choices in the Workflow speed menu, each a name and the settings it sets." },
  providerSets: { label: "Provider sets", help: "Named groups of settings that give each job a model, so jobs on different sets can run on different providers at the same time." },
  defaultProviderSet: { label: "Default provider set", help: "The provider set a job takes when none is chosen." },
  machine: { label: "This machine's name", help: "An older settings file's name for the default set's machine, still read; the machine is now the provider the set's models run on.", hidden: true },

  // Frames
  "frames.mode": { label: "Capture mode", help: "How frames are picked from a video: at a fixed rate, at scene changes, or whenever the shared screen changes." },
  "frames.perMinute": { label: "Frames per minute", help: "For the fixed rate mode: how many frames are taken from each minute of video." },
  "frames.sceneThreshold": { label: "Scene change size", help: "For the scene mode: how different two moments must look before a new scene starts." },
  "frames.minSceneSeconds": { label: "Shortest scene (seconds)", help: "For the scene mode: a shorter scene joins its neighbour." },
  "frames.maxCount": { label: "Most frames", help: "The most frames taken from one video, in every mode." },
  "frames.jpegQuality": { label: "Frame quality (1 to 100)", help: "How sharp the saved frames are; higher is sharper and makes bigger files." },
  "frames.changeToCapture": { label: "Change needed (0 to 1)", help: "For the screen change mode: how much of the screen must change before a frame is taken, from 0 to 1." },
  "frames.stillSeconds": { label: "Settle time (seconds)", help: "For the screen change mode: how long the screen must stay still after a change before the frame is taken." },
  "frames.busySeconds": { label: "Longest still-screen wait (seconds)", help: "For the screen change mode: how long the screen may keep moving before a frame is taken anyway." },
  "frames.boxEverySeconds": { label: "Screen search interval (seconds)", help: "For the screen change mode: how often the program looks again for where the shared screen sits." },
  "frames.passThroughSeconds": { label: "Skip screens shown only briefly (seconds)", help: "For the screen change mode: a repeated screen shown for less than this long is not taken." },
  "frames.buildStepChange": { label: "Slide build size (0 to 1)", help: "For the screen change mode: a frame that only adds a little to the one before it, such as a box or an arrow, counts as one step of the same slide." },
  "frames.faceModel": { label: "Face finder", help: "The model that finds faces in frames." },
  "frames.personModel": { label: "Person finder", help: "The model that finds people, so a lecturer walking in front of an unchanged board does not cause a new frame." },
  "frames.trimBars": { label: "Cut away screen bars", help: "Removes menu bars, title bars and taskbars at the edges of the shared screen." },
  "frames.maxWidth": { label: "Widest frame (pixels)", help: "Wider frames are scaled down; 0 keeps the recorded size." },
  "frames.tagsChars": { label: "Subject tag memory", help: "How many characters of earlier subject tags each frame call is shown, so it reuses them." },
  "frames.isKnowledgeFilterOn": { label: "Use the frame filter", help: "Drops frames that show only people, a gallery, or a blank or unreadable screen." },
  "frames.knowledgeClassifierCommand": { label: "Frame filter program", help: "The program that sorts each frame into people, text or other." },
  "frames.knowledgeClassifierWeights": { label: "Frame filter's trained data", help: "The trained data the frame filter program works with." },
  "frames.knowledgeFilterModel": { label: "Frame filter model", help: "Which model decides that a frame holds no knowledge." },
  "frames.decisionFilterPython": { label: "Helper for the decision filter", help: "The helper program that runs the decision filter model." },
  "frames.decisionFilterCut": { label: "Decision filter cut-off (0 to 1)", help: "Frames the decision filter scores below this chance of holding knowledge are dropped." },
  "frames.isTextOnlyMarkOn": { label: "Mark text-only frames", help: "Marks frames that show only printed text, so they are sent to the model as text instead of as a picture." },
  "frames.isBoardStageOn": { label: "Compare board frames", help: "Compares each task board frame with the board frame kept before it and drops the ones that barely changed." },
  "frames.boardHandler": { label: "Board display", help: "How the model is shown a changed board: the changed area enlarged, the whole frame with the text changes, or the text alone." },
  "frames.boardTextReader": { label: "Board text reader", help: "Which text reader reads a board's words: automatic, Apple's or Tesseract." },
  "frames.textReaderLanguages": { label: "Text reader languages", help: "The languages the text reader may recognise in frames." },
  "frames.isRedundancyFilterOn": { label: "Remove repeated frames", help: "A small vision model drops the frames that another frame already shows in full." },
  "frames.redundancyModel": { label: "Repeat-removal model", help: "The vision model that decides which frames are repeats." },
  "frames.redundancyFramesPerCall": { label: "Frames per repeat check", help: "How many consecutive frames the model looks at in one repeat check." },
  "frames.redundancyFrameWidth": { label: "Frame width for repeats (pixels)", help: "Frames are scaled to this width before the repeat check." },
  "frames.redundancyMaxMissingWords": { label: "Words allowed to go missing", help: "A removal stands only when the frames that keep its content show all but this many of the removed frame's words." },
  "frames.redundancyMinWords": { label: "Fewest words to check", help: "A removal stands only when the removed frame shows at least this many words, so photos and title cards are kept." },
  "frames.isSimilarityRecheckOn": { label: "Recheck similar frames", help: "After the repeat check, frames that look alike are compared once more by a model." },
  "frames.similarityThreshold": { label: "Similarity needed (0 to 1)", help: "How alike two frames must look before the model compares them." },
  "frames.similarityFramesPerCall": { label: "Frames per similarity check", help: "How many consecutive frames the model looks at in one similarity check." },
  "frames.similarityModel": { label: "Similarity model", help: "The model for the similarity recheck; empty uses the repeat-removal model." },
  "prompt.userPrompt": { label: "Your question", help: "What you want to know from the recording; it is added to the instruction every frame call gets." },
  "prompt.transcriptBeforeSeconds": { label: "Speech shown before a frame (seconds)", help: "How many seconds of earlier speech go with each frame; empty sends everything spoken before it." },
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
  "framesProcessing.minSentFrameWidth": { label: "Smallest frame width (pixels)", help: "Narrower frames are enlarged before they are sent; 0 sends every frame at its own size." },
  "framesProcessing.isTextOnlyFrameSentAsText": { label: "Send text-only frames as text", help: "Frames that show only printed text go to the model as the text read from them instead of as a picture." },

  // Audio
  "audio.engine": { label: "Speech engine", help: "Which program turns speech into text." },
  "audio.whisperModel": { label: "Speech model size", help: "The size of the speech recognition model; larger is more accurate and slower." },
  "audio.language": { label: "Spoken language", help: "Leave empty to detect the language; set one only for audio the detection gets wrong." },
  "audio.fasterWhisper.device": { label: "Processor for the standard speech engine", help: "Which processor the standard speech engine runs on." },
  "audio.fasterWhisper.computeType": { label: "Standard engine precision", help: "How precisely the standard speech engine calculates; finer is slower." },
  "audio.fasterWhisper.beamSize": { label: "Wordings tried per phrase (standard engine)", help: "How many wordings the standard speech engine weighs for each phrase; higher can be more accurate and slower." },
  "audio.fasterWhisper.vadMinSilenceMs": { label: "Silence to skip (milliseconds)", help: "Silences at least this long are skipped, which saves time on long meetings." },
  "audio.mlx.repositories": { label: "Where speech models are downloaded from", help: "Where each speech model size is downloaded from for the Mac speech engine." },
  "audio.mlx.chunkSeconds": { label: "Audio per call (seconds)", help: "How much audio goes to the graphics chip at once; values that are too long can freeze the Mac's screen." },
  "audio.mlx.noSpeechThreshold": { label: "Silence sensitivity (0 to 1)", help: "How sure the engine must be that a stretch holds no speech before it leaves it out." },
  "audio.mlx.conditionOnPreviousText": { label: "Use earlier text as a hint", help: "Feeds the text before a stretch into the next one, which keeps the wording consistent but can carry one bad passage forward." },
  "audio.diarization.isOn": { label: "Tell speakers apart", help: "Marks which speaker said each part of the recording." },
  "audio.diarization.model": { label: "Speaker model", help: "The model that finds who speaks when." },
  "audio.diarization.device": { label: "Speaker processor", help: "Which processor finds the speakers; automatic takes the best one this machine has." },
  "audio.diarization.minSpeakers": { label: "Fewest speakers", help: "The fewest speakers the model may find; empty lets it decide." },
  "audio.diarization.maxSpeakers": { label: "Most speakers", help: "The most speakers the model may find; empty lets it decide." },
  "audio.diarization.overlapShare": { label: "Overlap limit (0 to 1)", help: "A word gets no speaker when a second speaker covers at least this share of it." },
  "audio.takeaway.isDiarizationOn": { label: "Tell speakers apart in takeaways", help: "Whether a takeaway run separates the speakers; off is faster, since a fact needs what was agreed and not who said it." },
  "audio.takeaway.isThinkingOn": { label: "Think before takeaways", help: "Whether the model thinks before it writes a takeaway; off is faster, and on helps when facts come out wrong." },

  // Video
  "video.workflow": { label: "Video workflow", help: "How a video is processed when a run names no workflow: quality, direct draft or the earlier fast workflow." },
  "video.fastProcessing.frameModel": { label: "Frame describer", help: "The fast workflow describes frames with the model its provider set gives to frames processing.", hidden: true },
  "video.fastProcessing.frameFollowUps": { label: "Frames per session", help: "How many frames one description session handles before it starts fresh." },
  "video.fastProcessing.frameWidth": { label: "Frame width when describing (pixels)", help: "Frames are sent to the describer at this width." },
  "video.fastProcessing.isFrameThinkingOn": { label: "Think before describing a frame", help: "Whether the describer thinks before it describes a frame; off makes each frame a short call." },
  "video.fastProcessing.frameMaxTokens": { label: "Longest frame description (tokens)", help: "The most one frame's description may write, so a looping reply cannot run for minutes." },
  "video.fastProcessing.documentModel": { label: "Document writer", help: "The fast workflow writes the document with the model its provider set gives to document writing.", hidden: true },
  "video.fastProcessing.isDocumentThinkingOn": { label: "Think before writing", help: "Whether the document writer thinks before it writes." },
  "video.directDraft.frameWidth": { label: "Frame width when writing (pixels)", help: "Frames are sent to the writer at this width." },
  "video.directDraft.lastSectionChars": { label: "Earlier text shown (characters)", help: "How much of the draft's last section each batch is shown, so long sections do not slow every call." },
  "video.directDraft.firstLineChars": { label: "Section start shown (characters)", help: "How much of each section's start the writer reads beside its heading." },
  "video.directDraft.outlineCalls": { label: "Most outline calls", help: "The most calls the outline conversation may make, including every look, cut and mark on a frame." },

  // Document
  "document.chunkEntries": { label: "Notes per outline call", help: "How many consecutive notes one outline call sorts into topics." },
  "document.chunkCharacters": { label: "Notes size per outline call (characters)", help: "The most characters of notes one outline call is given." },
  "document.momentSpeechChars": { label: "Speech per moment (characters)", help: "How much speech is shown with each moment when the agenda is made." },
  "document.foldEntries": { label: "Short returns merged", help: "A later topic of at most this many notes whose title appeared before is written where the subject first came up; 0 merges nothing." },
  "document.sectionEntries": { label: "Notes per section call", help: "How many notes one section call writes under a single heading." },
  "document.chapterEntries": { label: "Notes per chapter part", help: "A chapter is written in parts of at most this many notes." },
  "document.headingEntries": { label: "Notes per heading", help: "A heading with more notes than this is written in even parts under the same heading." },
  "document.summaryMaxWords": { label: "Longest summary (words)", help: "The most words the executive summary may have." },
  "document.summaryMinWords": { label: "Shortest summary (words)", help: "The fewest words the executive summary may have, however short the document." },
  "document.minCutPixels": { label: "Smallest frame cut (pixels)", help: "A cut whose shorter side is below this is flagged to the writer, who decides whether it matters." },
  "document.writeMode": { label: "Writing method", help: "Whether the document is written in one conversation of follow-ups or in separate calls for each part." },
  "document.conversationSizeLimitTokens": { label: "Writing conversation size limit (tokens)", help: "Past this size the writing conversation is summarised and goes on from the summary." },
  "document.conversationWindowTokens": { label: "Fallback context window (tokens)", help: "The size the conversation keeps within when the server does not report its own window." },
  "document.readChars": { label: "Characters per read", help: "The most characters one read of the notes returns." },
  "document.notesChars": { label: "Writer's notes size (characters)", help: "The most the writer's own notes may hold together." },
  "document.charsPerToken": { label: "Characters per token, writing", help: "How many characters the program counts as one token when it estimates sizes between the server's own counts." },
  "document.frameTokens": { label: "Tokens per frame", help: "How many tokens the program counts for one frame sent to the model when it estimates sizes." },
  "pdf.defaultMode": { label: "How PDFs are read", help: "How a PDF is read when its run chooses nothing; by size reads short ones as page pictures and long ones as converted text." },
  "pdf.imagesPageLimit": { label: "Most pages as pictures", help: "The most pages a PDF may have to be read as page pictures when the mode depends on size." },
  "pdf.modelWritingPageLimit": { label: "Most pages written by a model", help: "The most pages a PDF may have to be written by a model when the mode depends on size; a longer one is converted to text." },
  "pdf.ocrEngine": { label: "Scan reader", help: "What reads pages whose words are only pixels, such as scans." },
  "pdf.unlimitedOcrPython": { label: "Scan reader program", help: "The program that runs the Unlimited-OCR scan reader." },
  "pdf.unlimitedOcrModel": { label: "Scan reader model", help: "The model the Unlimited-OCR scan reader downloads and uses." },
  "pdf.figureReading": { label: "Which figures are read", help: "Which figures of a PDF a model reads; the others are placed in the document where they stood, unread." },
  "pdf.figureReadLimit": { label: "Most figures read", help: "The most figures a model reads when figures above a limit are placed unread." },
  "pdf.cleanupWishes": { label: "Extra instructions for the clean up", help: "Added to the clean up of a PDF's document, such as removing every ticket link." },
  "pdf.rewriteWishes": { label: "Extra instructions for the rewrite", help: "Added to the rewrite of a PDF's document." },
  "pdf.knowledgeWishes": { label: "Extra instructions for the knowledge pass", help: "Added to the knowledge pass over a PDF's document." },
  "tags.nameRules": { label: "Tag naming rules", help: "Rules that turn a recording's name pattern into a tag, applied to every run." },
  "tags.workerTags": { label: "Tags added at the end", help: "The most tags the last step of document writing adds, each reused from earlier runs or new when none fits." },
  "documentReshaping.batchTokens": { label: "Part size (tokens)", help: "The least a part holds; a part ends at the first heading past it, so a section is never cut." },
  "documentReshaping.batchCalls": { label: "Calls per part", help: "The most model calls one part may make before it is blocked." },
  "documentReshaping.conversationSizeLimitTokens": { label: "Reshaping conversation size limit (tokens)", help: "Past this size a part's conversation is summarised and goes on from the summary." },
  "documentReshaping.firstWarningPercent": { label: "First warning at (percent full)", help: "The share of the size limit at which the worker is told its conversation is filling up." },
  "documentReshaping.secondWarningPercent": { label: "Second warning at (percent full)", help: "The share of the size limit at which the worker is warned again and may drop results it no longer needs." },
  "documentReshaping.charsPerToken": { label: "Characters per token, reshaping", help: "How many characters the program counts as one token when it sizes parts and warnings." },

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
  "budgets.cutsPerFrame": { label: "Cuts per frame", help: "The most cuts that may be in use on one frame." },
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
  "queue.retryPauseSeconds": { label: "Pause before a retry", help: "How many seconds a failed job waits before it runs again, so a restarting provider has time to come back." },
  "queue.onFailure": { label: "After a failed job", help: "What happens after a job's last try: continue starts the next job, block holds the queue until you retry or skip the job." },
};

// The page's sections, by the person's job; each top-level key prefix of the settings is in exactly one group
export const SECTION_GROUPS: SectionGroup[] = [
  {
    id: "machines-and-models",
    title: "Model servers",
    intro: "Where each model runs, which models there are, and which model each job uses.",
    prefixes: ["machine", "hosts", "providerSets", "defaultProviderSet", "models", "phaseModels", "providers"],
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

// A section that holds many settings is split into cards by what the settings do, never one lump under its own title. A key
// belongs to the first card listing it, or listing the group it stands in ("audio.mlx" holds "audio.mlx.chunkSeconds");
// a key no card lists falls in a last card of the section's other settings. A section with no list is split by the first part
// of its keys.
export type SubGroup = { title: string; keys: string[] };

export const SECTION_SUBGROUPS: Record<string, SubGroup[]> = {
  frames: [
    { title: "Frame capturing", keys: ["frames.mode", "frames.perMinute", "frames.sceneThreshold", "frames.minSceneSeconds",
      "frames.maxCount", "frames.jpegQuality", "frames.maxWidth", "frames.trimBars", "frames.faceModel", "frames.personModel"] },
    { title: "Screen watching", keys: ["frames.changeToCapture", "frames.stillSeconds", "frames.busySeconds",
      "frames.boxEverySeconds", "frames.passThroughSeconds", "frames.buildStepChange"] },
    { title: "Frames preparing", keys: ["frames.isKnowledgeFilterOn", "frames.knowledgeClassifierCommand",
      "frames.knowledgeClassifierWeights", "frames.knowledgeFilterModel", "frames.decisionFilterPython", "frames.decisionFilterCut",
      "frames.isRedundancyFilterOn", "frames.redundancyModel", "frames.redundancyFramesPerCall", "frames.redundancyFrameWidth",
      "frames.redundancyMaxMissingWords", "frames.redundancyMinWords", "frames.isSimilarityRecheckOn",
      "frames.similarityThreshold", "frames.similarityFramesPerCall", "frames.similarityModel"] },
    { title: "Text and boards", keys: ["frames.isTextOnlyMarkOn", "framesProcessing.isTextOnlyFrameSentAsText",
      "frames.isBoardStageOn", "frames.boardHandler", "frames.boardTextReader", "frames.textReaderLanguages"] },
    { title: "What the model is told", keys: ["prompt", "frames.tagsChars"] },
    { title: "Frames processing", keys: ["framesProcessing.framesPerCall", "framesProcessing.isCritiqueOn",
      "framesProcessing.isCritiqueRepairOn", "framesProcessing.isPdfCritiqueOn", "framesProcessing.isPdfFigureCritiqueOn",
      "framesProcessing.isOpenQuestionsOn", "framesProcessing.isPdfOpenQuestionsOn", "framesProcessing.repairCalls",
      "framesProcessing.singleFrameAttempts", "framesProcessing.minSentFrameWidth"] },
  ],
  audio: [
    { title: "Speech to text", keys: ["audio.engine", "audio.whisperModel", "audio.language", "audio.fasterWhisper.vadMinSilenceMs",
      "audio.mlx.chunkSeconds", "audio.mlx.noSpeechThreshold", "audio.mlx.conditionOnPreviousText"] },
    { title: "Speech engine details", keys: ["audio.fasterWhisper.device", "audio.fasterWhisper.computeType",
      "audio.fasterWhisper.beamSize", "audio.mlx.repositories"] },
    { title: "Speaker detection", keys: ["audio.diarization"] },
    { title: "Takeaways", keys: ["audio.takeaway"] },
  ],
  video: [
    // the workflow and the speed menu are one setting each: one card holds both, never a card of one setting
    { title: "Workflow and speed choices", keys: ["video.workflow", "speedPresets"] },
    { title: "Describing frames", keys: ["video.fastProcessing.frameFollowUps", "video.fastProcessing.frameWidth",
      "video.fastProcessing.isFrameThinkingOn", "video.fastProcessing.frameMaxTokens", "video.fastProcessing.frameModel"] },
    { title: "Writing the document", keys: ["video.fastProcessing.documentModel", "video.fastProcessing.isDocumentThinkingOn",
      "video.directDraft"] },
  ],
  document: [
    { title: "Outline", keys: ["document.chunkEntries", "document.chunkCharacters", "document.momentSpeechChars",
      "document.foldEntries"] },
    { title: "Chapters", keys: ["document.sectionEntries", "document.chapterEntries", "document.headingEntries",
      "document.summaryMaxWords", "document.summaryMinWords", "document.minCutPixels"] },
    { title: "Writing conversation", keys: ["document.writeMode", "document.conversationSizeLimitTokens",
      "document.conversationWindowTokens", "document.readChars", "document.notesChars", "document.charsPerToken",
      "document.frameTokens"] },
    { title: "Document reshaping", keys: ["documentReshaping"] },
    { title: "PDFs", keys: ["pdf"] },
    { title: "Tags", keys: ["tags"] },
  ],
  "review-and-quality": [
    // the critique collection is one setting: it joins the review's card
    { title: "Checking the finished document", keys: ["review", "critiqueCollection"] },
  ],
  "advanced-limits": [
    { title: "Tries and stopping", keys: ["budgets.maxAttempts", "budgets.askWhyAfterRejection", "budgets.runSteps",
      "budgets.writeChapterAttempts", "budgets.failOnFrameCutFail"] },
    { title: "Writing limits", keys: ["budgets.chapterDryRuns", "budgets.writeCalls", "budgets.writeHeadingsCalls",
      "budgets.writeReads", "budgets.writeChapterCalls"] },
    { title: "Frame cuts", keys: ["budgets.cutsPerFrame", "budgets.undosPerFrame", "budgets.gridLinesMax"] },
    { title: "Queue", keys: ["queue"] },
  ],
};

// The card of a section a key stands in, or undefined when the section has no cards listed or none lists the key
export function findSubgroupTitle(sectionId: string, key: string): string | undefined {
  return SECTION_SUBGROUPS[sectionId]?.find((group) => group.keys.some((each) => key === each || key.startsWith(`${each}.`)))?.title;
}

// The first part of a key: "frames.perMinute" gives "frames"
export function readSettingPrefix(key: string): string {
  const dot = key.indexOf(".");
  return dot < 0 ? key : key.slice(0, dot);
}

// The words a menu shows for each stored value of a setting, so the page never offers a code word as a choice
export const CHOICE_LABELS: Record<string, Record<string, string>> = {
  "frames.mode": { interval: "At a fixed rate", scene: "At scene changes", change: "When the screen changes" },
  "frames.knowledgeFilterModel": { "google-SigLIP-2": "Google's picture sorter", "liquidai-d1-2B": "Liquid AI's picture sorter" },
  "frames.boardHandler": { "image-diff-only": "The changed area enlarged", "text-diff-integration": "The frame with its text changes",
    "text-only-integration": "The text alone" },
  "frames.boardTextReader": { auto: "Automatic", apple: "Apple's reader", tesseract: "Tesseract" },
  "audio.engine": { "faster-whisper": "Standard speech engine", mlx: "Speech engine for Mac" },
  "audio.fasterWhisper.device": { auto: "Automatic", cpu: "Main processor", mps: "Mac graphics chip", cuda: "NVIDIA graphics card" },
  "audio.diarization.device": { auto: "Automatic", cpu: "Main processor", mps: "Mac graphics chip", cuda: "NVIDIA graphics card" },
  "video.workflow": { quality: "Quality", "direct-draft": "Direct draft", "fast-processing": "Fast" },
  "pdf.defaultMode": { "by-size": "By size", "pages-as-images": "Pages as images", "text-only": "Text only",
    "markdown-conversion": "Converted to text" },
  "pdf.ocrEngine": { tesseract: "Tesseract", "unlimited-ocr": "Unlimited-OCR" },
  "pdf.figureReading": { "read-all": "Read every figure", "place-above-limit": "Read up to the limit, place the rest unread",
    "read-drawings-only": "Read drawings only" },
  "document.writeMode": { calls: "Separate calls for each part", conversation: "One conversation of follow-ups" },
  "queue.onFailure": { continue: "Start the next job", block: "Hold the queue" },
};

// The label of one stored value of a setting, or the value itself when no words are written for it
export function readChoiceLabel(key: string, value: string): string {
  const field = key.startsWith("models.") ? key.slice(key.lastIndexOf(".") + 1) : "";
  return CHOICE_LABELS[key]?.[value] ?? MODEL_FIELD_CHOICE_LABELS[field]?.[value] ?? value;
}

// The words for the values of a field every model has, by the field's name
const MODEL_FIELD_CHOICE_LABELS: Record<string, Record<string, string>> = {
  structuredMethod: { function_calling: "Function calls", json_schema: "A fixed answer shape", json_mode: "Plain JSON text" },
};

export type FixedChoice = { value: string; label: string; hint: string };

// The settings whose values come from a short fixed list although the server sends them as free text: each is chosen from a
// menu, one row a choice with a grey line under its name. `isOpen` also offers "Other…" for a value of one's own
export const FIXED_CHOICES: Record<string, { options: FixedChoice[]; isOpen?: boolean }> = {
  "audio.fasterWhisper.device": { options: [
    { value: "cpu", label: "Main processor", hint: "works on every computer" },
    { value: "cuda", label: "NVIDIA graphics card", hint: "much faster when the computer has one" },
    { value: "auto", label: "Automatic", hint: "takes the graphics card when there is one" },
  ] },
  "audio.fasterWhisper.computeType": { options: [
    { value: "float32", label: "Most precise", hint: "slowest, the most memory" },
    { value: "float16", label: "Precise", hint: "needs a graphics card" },
    { value: "int8_float16", label: "Fast and precise", hint: "needs a graphics card" },
    { value: "int8", label: "Fastest", hint: "least precise, the least memory" },
  ] },
  "audio.whisperModel": { options: [
    { value: "tiny", label: "Tiny", hint: "fastest, least accurate" },
    { value: "base", label: "Base", hint: "fast, simple recordings" },
    { value: "small", label: "Small", hint: "a good start for clear speech" },
    { value: "medium", label: "Medium", hint: "more accurate, slower" },
    { value: "large", label: "Large", hint: "accurate, slow" },
    { value: "large-v3", label: "Large, third version", hint: "accurate, slow" },
    { value: "large-v3-turbo", label: "Large, fast version", hint: "nearly as accurate as large, much quicker" },
  ] },
  "audio.diarization.model": { isOpen: true, options: [
    { value: "pyannote/speaker-diarization-community-1", label: "Community speaker finder",
      hint: "the standard one; its terms are accepted once on its web page" },
  ] },
};

// The languages the text reader may be given, by the name it knows each by
export const TEXT_READER_LANGUAGES: FixedChoice[] = [
  { value: "eng", label: "English", hint: "" }, { value: "deu", label: "German", hint: "" },
  { value: "fra", label: "French", hint: "" }, { value: "spa", label: "Spanish", hint: "" },
  { value: "ita", label: "Italian", hint: "" }, { value: "nld", label: "Dutch", hint: "" },
  { value: "por", label: "Portuguese", hint: "" }, { value: "pol", label: "Polish", hint: "" },
  { value: "rus", label: "Russian", hint: "" }, { value: "tur", label: "Turkish", hint: "" },
  { value: "ara", label: "Arabic", hint: "" }, { value: "jpn", label: "Japanese", hint: "" },
  { value: "chi_sim", label: "Chinese, simplified", hint: "" },
];

// The choices a setting's value may be picked from, as menu options: the fixed list, else the words written for its values;
// a value the list does not hold stays in it, so a value set in a file still shows
export function listFixedChoices(key: string, current: string): { options: FixedChoice[]; isOpen: boolean } | null {
  const fixed = FIXED_CHOICES[key];
  const words = CHOICE_LABELS[key];
  const options = fixed ? fixed.options : words ? Object.entries(words).map(([value, label]) => ({ value, label, hint: "" })) : null;
  if (!options) return null;
  const kept = current === "" || options.some((option) => option.value === current)
    ? options : [...options, { value: current, label: current, hint: "set in your own file" }];
  return { options: kept, isOpen: Boolean(fixed?.isOpen) };
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
