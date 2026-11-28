# Flow: from the video to the document

Each line says who acts, what it does, and on which file. "Program" is the code of the named node. "Model" is the local language
model qwen3.6-35b-a3b-mtp in LM Studio, called in one conversation at a time; it remembers nothing between calls. A "frame" is the
screenshot of the video at one moment, saved in `frames/`.

```
PHASE 1  transcribe
  actors   program = transcribe_audio_node, with mlx-whisper large-v3-turbo (speech recognition, no language model)
  program  writes   ingesting-profile.json   before phase 1, from `run --ingesting-profile` (the run form's Ingesting
                                         profile), chosenBy user; no tag and no model call changes it after
  program  writes   run-medium.json      before phase 1, from `run --as-audio` (the run form's Process as: Audio): a
                                         video then takes the audio workflow below, its sound alone, no frame taken
  program  writes   run-speed.json       at the run's start (and the server when it queues a job naming one), from
                                         `run --speed-preset/--speed-setting` (the Workflow speed menu): the preset and
                                         the values it sets, laid over the settings at every start; it writes
                                         run-workflow.json too. Every later phase reads the settings it changed
  program  writes   run-workflow.json    at every start of a video run, the workflow of the speed kept in run-speed.json
                                         (a speed changed on the page while the run worked kept only the speed), unless
                                         `run --workflow` or a workflow chosen by hand says otherwise
  program  removes  phase 3 and later    at every start of a video run whose phase 3 files (03_notes.md and
                                         03_processed-frames/, 03_draft.md, or 03_frame-descriptions.md) belong to
                                         another workflow than the one it starts on: the run goes back to phase 2, as
                                         reset-to 2 does, and the console prints a warning naming both workflows
  program  writes   run-input-range.json  before phase 1, from `run --start/--end` or `run --pages` (the run form's
                                         Range): the part of the input the run processes; none: the whole input
  program  reads    run-input-range.json  a time window: only the window's audio is cut out of each part it crosses
                                         and transcribed, its times moved onto the recording's clock
  program  reads    the video, or each part of a folder of parts in name order, its times moved on by the parts before it
  program  runs     pyannote speaker-diarization-community-1 once over the whole meeting's audio, every part at its
                    offset, while audio.diarization.isOn (speaker recognition, no language model)
  program  writes   01_transcript.json   the speech, in timed segments; with diarization each segment and word has its
                                         speaker ("Speaker A" … in the order they first speak, null for speech two
                                         people said at once), a segment split where its speaker changes, and
                                         "diarization" says done, off, unavailable or failed (the last two keep
                                         Whisper's segments with no speaker), and when done holds pyannote's
                                         turns and exclusive turns, named as the segments' speakers
  program  writes   audio.wav

  For a PDF phase 1 reads the file instead, with no speech recognition:
  program  reads    run-pdf-ocr-engine.json  the engine the run chose for scanned pages (`run --pdf-ocr-engine`); none:
                                         pdf.ocrEngine
  program  reads    run-input-range.json  a span of pages: only those pages are read, each keeping its own number and
                                         minute; the lines and blocks repeated on most pages are still found over the
                                         whole PDF
  program  reads    the PDF: each page's text through pymupdf4llm (Tesseract for a page whose words are pixels), its
                    links to outside pages through PyMuPDF; with the engine unlimited-ocr, each scanned page (a picture fills it, no word in ink) rendered 1,600 pixels wide and
                    read by Unlimited-OCR-2 instead (tools/unlimited-ocr/read_pages.py, its own environment)
  program  writes   logs/<run>/transcribe.json   the reading's six stages, each counted page by page as it goes: the
                                         text layer, the figures, the reader's parsing, its markdown, the scanned
                                         pages, the page cleaning (one not needed is skipped), with when each last
                                         counted a page; read by the page's phase 1 bar and stage table, and the
                                         console prints a step's count every 100 pages
  program  writes   01_transcript.json   each page's cleaned text as segments, one per block, each at its page's
                                         minute (page 3 is minute 2), its page on it, no speaker; `ocrEngine` and
                                         `ocrPages` name the scanned pages Unlimited-OCR-2 read, and a scanned page's
                                         pictures (1% of the page or more) are among `figures`, which no later step
                                         tells apart
  program  writes   01_cleanup.json      every line, block and picture text taken out, with its page and its rule (a
                                         block at the same place on most pages, a line repeated on most pages, export
                                         chrome from agent-context/pdf/chrome-lines.txt, a picture's text of a few
                                         words, the document's own table of contents, and what Unlimited-OCR-2 labels
                                         a header, footer or page number on a scan)

PHASE 2  frames_preparing
  actors   program = prepare_frames_node (plain code, no language model)
  program  reads    the video, or a folder's parts as one video, frame numbers running on
  program  reads    run-input-range.json  a time window: sampling starts at its start (sought, not decoded up to) and
                                         stops at its end, each frame keeping its time on the recording's clock; a PDF's
                                         span of pages: only those pages rendered, frames numbered from 0, each with its
                                         own page
  program  writes   frames/*.jpg         one frame per moment the shared screen changed; a return to an earlier screen
                                         is marked repeatOf it, and one on screen under 5 s (a pass-through) is not taken;
                                         a frame that changed little from the one just before, or only added to it
                                         on its empty space, is marked stepOf the slide's first frame (one slide
                                         built up step by step)
  program  writes   frames/.rules-result.json  the frames the rules took, with the run's id and the frame settings:
                                         a resume of the same run reads them back instead of taking them again,
                                         the images a later stage moved into frames/dropped/ put back first
  program  runs     the frame classifier (frame-classifier/, its own command, `--task tags`)
                                         once on every frame taken, while frames.isKnowledgeFilterOn,
                                         frames.isTextOnlyMarkOn or frames.isBoardStageOn: each frame tagged people,
                                         text, other or board, with its people score
  program  reads    run-knowledge-filter.json  the knowledge filter's model the run chose (`run --knowledge-filter-model`);
                                         none: frames.knowledgeFilterModel
  program  runs     the d1-2B knowledge filter (tools/d1-knowledge-filter/, its own environment) when that model is
                                         liquidai-d1-2B: each frame's knowledge probability and empty-frame classes;
                                         a frame under frames.decisionFilterCut is tagged people, with its dropClass;
                                         a chat model named there instead is asked over /v1/chat/completions, one
                                         call per frame and question, its reply's first number read as the score
  program  writes   frames/dropped/*.jpg  the frames tagged people (no knowledge), moved out of frames/, while
                                         frames.isKnowledgeFilterOn; shown on the detail view as "Dropped frames"; then
                                         each frame between two showings of one screen (the next frame repeats the one
                                         before it) with a people score of 0.6 or more (droppedBy cut-away, coveredBy
                                         the frame before): a broadcast cut to the speaker
  program  reads    01_transcript.json   the end of the talk's last segment of substance (three words or more that are no
                                         thanks); the frames taken after it, a broadcast's closing cards, go to
                                         frames/dropped/ (droppedBy after-talk), always, before any model reads them;
                                         a frame taken before its first segment of substance on which neither text
                                         reader finds 3 words, an opening animation, goes there too (droppedBy
                                         before-talk); so does a frame whose inside, its top 9% and bottom 8% left
                                         out, is one flat colour while both those bands hold a bar, a cleared
                                         terminal (droppedBy empty-screen)
  program  writes   frames/dropped/*.jpg  a build step (stepOf) whose changed blocks held content on the frame before it and
                                         that shows no word of four letters or more that frame lacks (droppedBy
                                         empty-step), always: a drawn hand moved, the same slide zoomed, or the
                                         frame before moved or scaled, unchanged once lined up
  program  writes   frames/dropped/*.jpg  a build's first frame on screen at most frames.passThroughSeconds before its
                                         next step, which only adds on empty background and shows its every word
                                         (droppedBy pass-through, coveredBy the step, which starts the build), always
  program  writes   frames/dropped/*.jpg  a frame no step joined that is the kept frame before it moved or scaled,
                                         unchanged once lined up, its words read there (droppedBy moved-copy), always
  program  writes   frames/dropped/*.jpg  a return the capture marked repeatOf that adds nothing to its first showing:
                                         no marks where that frame is blank, and either Tesseract's words all there
                                         but frames.redundancyMaxMissingWords or no line Apple's recognizer reads
                                         that it or the 12 kept frames before lack, of at least
                                         frames.redundancyMinWords words; a 3-minute re-take (isHeldRetake in
                                         02_frames.json) stays (droppedBy repeat-copy, coveredBy the first showing),
                                         always
  program  writes   frames/dropped/*.jpg  the board stage, while frames.isBoardStageOn: a board frame under 1% changed from
                                         the board frame kept before it (droppedBy board, coveredBy that frame); one under
                                         40% changed is kept as a step of the board (stepOf) with boardChange, the box
                                         of the blocks that changed; every board frame kept gets isBoard
  program  reads    the recording        with frames.boardHandler "text-diff-integration" (the default): each board
                                         step's boxed part, at full size, on it and the board frame before it, read by
                                         frames.boardTextReader's reader (Apple's on a Mac, else
                                         Tesseract); writes the step's boardTextDiff (added, changed, removed
                                         lines, a scroll)
  model    reads    frames/*.jpg         the kept frames, frames.redundancyFramesPerCall at a time, while
                                         frames.isRedundancyFilterOn (frames.redundancyModel, frame_redundancy.md)
  program  writes   frames/dropped/*.jpg  the frames the redundancy filter's checked answer says another kept frame
                                         holds whole
  program  writes   frames/dropped/*.jpg  the similarity recheck, while frames.isSimilarityRecheckOn, first with no model:
                                         a kept frame alike to an earlier kept one (the vectors below) that adds no marks,
                                         no number Apple's recognizer reads, and nothing either reader finds new on it
                                         (Tesseract missing no word), a 3-minute re-take excepted (droppedBy similarity)
  model    reads    frames/*.jpg         then the kept frames that look
                                         alike (the classifier's SigLIP vectors, logs/<run>/frame-vectors.npz, mean cosine
                                         at or above frames.similarityThreshold) but sat in different batches of the
                                         redundancy filter, grouped, frames.similarityFramesPerCall a call, same instruction
  program  writes   frames/dropped/*.jpg  the frames the recheck's checked answer says another kept frame holds whole
                                         (droppedBy similarity)
  program  writes   logs/<session>/frames_preparing/<layer>-<first frame>-<last frame>/attempt-1/  each call of those two
                                         layers: system-prompt.md, human-message.md, images.json (the frame each image is),
                                         reply.md, decision.json; read only by the detail view's "What the worker saw"
  program  writes   frames/dropped/dropped-frames.json  after each filter, the entries of every frame dropped so
                                         far (droppedBy, score, coveredBy, reason), read only by the detail view
                                         until 02_frames.json exists
  program  writes   02_frames.json       one entry per kept frame: its time, its file, its numbers, its drop score (the
                                         people score) and tag, the
                                         words spoken over it, each change of speaker named ("Speaker B: …") when
                                         phase 1 placed speakers (a dropped frame's words go to the kept frame before
                                         it); and under droppedFrames each dropped frame's time, file, drop score, tag and droppedBy
                                         (filter; board, with coveredBy, when the board stage dropped it;
                                         empty-step, pass-through or moved-copy, with coveredBy, the kept frame it
                                         adds nothing to;
                                         redundancy, with coveredBy and the model's reason, when the
                                         redundancy filter dropped it; refine or hand when dropped later from the
                                         detail view or `i2k refine-frames` / `drop-frames`, before phase 3 reads it)
  program  writes   logs/<run id>/frames-preparing.json   the stages in order (rules, classifier, model), each
                                         waiting, running or done with how far it is (seconds of video sampled, frames
                                         judged), written as it changes; the detail view's bar for phase 2 reads it.
                                         Each stage that ends prints "stage N of M done (<stage>): <frames> frame(s) in
                                         <s> s, <s> s a frame" on the console
  program  writes   02_frames.json       each kept frame's isTextOnly (tagged text: it holds text and nothing that
                                         carries meaning beside it) and textOnlyScore (1 when it passes the ink rule's
                                         cuts), while frames.isTextOnlyMarkOn
  program  reads    the recording        each kept frame stored at least 1.3 times smaller than the part it shows,
                                         at that frame's number, read by Tesseract at its own size
  program  writes   02_frames.json       that frame's screenWords, the words read there, in the stored frame's pixels

PHASE 2  frames_preparing, for an audio recording
  actors   program = prepare_speech_node, model = phase 2's (`document` by default), ONE call
  program  reads    01_transcript.json, and run-tags.json (the tags given on the run line, the run form or the detail view)
  program  writes   ingesting-profile.json   the run's ingesting profile, when a tag names one or one of its aliases
                                         ("chat" is conversation), chosenBy tag; a profile the person or a conversion
                                         chose is kept
  program  sends    every transcript line with its id (S0001 on) and time, grouped under each speaker's turn when
                    phase 1 placed speakers (a line said at once in the turn before it), the tags, and the profiles to
                    pick from when no tag named one
  model    writes   the speech as paragraphs, each one speaker's turn on one point, clean sentences, under
                    "## S<first>-S<last> · Speaker <letter>", every line in exactly one paragraph, each paragraph
                    inside one turn and named for its speaker; and "Profile: <name>" when asked
  program  checks and writes   02_speech.md   (every line once and in order, the speakers' shape, each
                                         paragraph inside one turn and named for its speaker, no paragraph a
                                         summary or longer than its lines, the profile one of the list)
  program  writes   ingesting-profile.json   the profile the model picked, chosenBy model, when no tag named one and
                                         the person chose none
  program  writes   02_frames.json       with no item and "medium": "audio": no frame is taken; phase 3 is recorded
                                         complete with nothing to read, unless the profile declares preparation steps
  takeaway the profile `takeaway` makes no call here: 02_frames.json is written empty and phase 3 closed, with no
           02_speech.md; phase 1 before it told no speakers apart (audio.takeaway.isDiarizationOn, off)

PHASE 3  frames_processing, for an audio recording whose ingesting profile declares preparation steps
  actors   program = prepare_notes_node, model = phase 3's (`frame` by default), one call per step over the whole speech, or per item
           of an earlier step
  program  reads    agent-context/preprocessing/<profile>.json   the steps: each its id, title, input (`speech`, or
                                         `each:<step>`) and sections; none below its `minParagraphs` paragraphs
  program  reads    02_speech.md, ingesting-profile.json and run-tags.json
  program  sends    the step's sections, and the whole speech's paragraphs, or one item's paragraphs with what the
                    earlier step wrote about it; a later step over the whole speech gets every earlier step's file
  model    writes   "## <item>" per item, each "### <section>" of its step ending with the paragraph ids it rests on
  program  checks and writes   03_prepared/<step>.md, or 03_prepared/<step>/NN.md per item   (every section, ids of
                                         the request's paragraphs only, numbers its paragraphs hold, every paragraph in
                                         an item for a step a later step details, one item for a call about one item)
  program  passes over   a call whose file is there and passes its check: a resume asks only what is missing

PHASE 3  frames_processing
  actors   program = process_frames_node, model = phase 3's (`frame` by default)
  program  reads    02_frames.json and the frames
  program  reads    each frame's isTextOnly: while framesProcessing.isTextOnlyFrameSentAsText, a text-only frame on which
                    the text reader finds lines is sent as those lines instead of its image, to the batch and its follow-ups
  program  reads    each frame's boardChange: with frames.boardHandler "image-diff-only", a board step whose changed box
                    covers at most half the frame is sent as that box cut out and enlarged to
                    framesProcessing.minSentFrameWidth, with a line saying so
  program  reads    each frame's boardTextDiff: with "text-diff-integration", a board step is sent whole with the
                    changes listed in its message
  program  reads    each frame's image for an editor split one pane above the other: each pane's status line found
                    (read as one, or a band one text line tall) and read alone for its file; the message names each pane's file
  program  reads    each frame's screenWords, when phase 2 wrote them: the lines its message lists, its `## Screen` is
                    checked against and its outline is completed with, in place of a reading of the stored frame
  program  sends    a batch of frames, their words and the earlier notes to the model; a frame showing an earlier screen
                    again also gets that showing's notes and words, and its notes say what this showing adds; a slide
                    built up step by step stays in one batch, outlined whole once, then what each step adds
  model    writes   each frame's notes, then the batch's title, story, atmosphere, questions and answers; a frame's
                    `## As markdown` holds the screen's code, table or list as the markdown block phase 4 writes into
                    the document in place of a picture (a `markdown` note in 03_notes.md)
  program  checks and saves   the notes into each frame's entry in 02_frames.json
  program  saves    the batch's title, story, atmosphere, questions and answers in 03_batch_summaries.json
  program  writes   03_processed-frames/frame-NNN.md and frames-A-B-batch-summary.md   each holding everything its entry in the two files above holds (notes and data), read by nobody
  model    writes   after each batch, in this order, each its own call:
                    1. the critique        what went WRONG in this batch's notes, each mistake as a rule for ANY meeting
                                           (a name, what it is, how to avoid it, the frames it was seen on). It changes
                                           nothing     ─► 03_processed-frames/frames-A-B-critique.md
                                           (framesProcessing.isCritiqueOn; a rule naming a frame in its own lines, or
                                           quoting this meeting's screen, is refused where it is written; a PDF's
                                           batches skip it and the repair, framesProcessing.isPdfCritiqueOn off)
                    2. the repair          the notes fixed FROM those rules, adding only
                                           (framesProcessing.isCritiqueRepairOn; off, the critique stands and the notes
                                           stay as the first call wrote them)
                    3. the open questions  what a reader still cannot know
                    4. the answers         found in the notes, the words and the frames
                    a step cut at its output limit while thinking: one more call compacts that thinking into
                    the attempt's compaction.md, and the next attempt opens with it
  program  writes   03_notes.md          a copy of the joined text with the batch sections, for you; a speech line
                                         printed with its speaker when phase 1 placed one

PHASE 3  frames_processing, for a direct-draft video run (the Fast speed)
  actors   program = process_frames_node + direct_draft_batch_service, model = phase 3's (the same as Quality's), one
           call per batch of framesProcessing.framesPerCall frames (8), no tools
  program  reads    02_frames.json and frames/*.jpg; logs/worker-messages/frames_processing.jsonl, what the person
                                         wrote to the worker, added before the next batch
  program  writes   03_draft.md          before the first batch: "# Draft of <recording>"; then each batch appends
                                         its marker line and the text the model wrote (the draft's format is below)
  program  sends    the instruction (video/direct_draft_batch.md and the ingesting profile's section), the recording's
                    name and tags, the draft's headings so far (H01 ## Title ...), the draft's last section (at most
                    video.directDraft.lastSectionChars characters), then the batch's frames, each as a line (number,
                    time, the speech said while it is on screen) and its image
  model    writes   markdown: it continues the last section, or opens "## Chapter" and "### Section" headings itself;
                    a picture it wants in the document is a line of its own, {{frame-017: caption}}; nothing is
                    checked, no critique, no repair, no frame notes
  program  writes   logs/fast-turns/fast-notes-<first>-<last>.jsonl   each call as a turn, read only by the page's
                                         worker panels; each frame's status "written" in the run store
  program  writes   no notes in 02_frames.json, no 03_notes.md, no 03_frame-descriptions.md
  the text is appended before the frames are marked written, so a stop loses no answered batch; a resume continues
  with the frames not yet written, and a batch's number is the count of markers already in 03_draft.md plus one.

  The draft, 03_draft.md:
    # Draft of Gateway-demo.mp4
    <!-- batch 1 · frames 0-7 -->            one marker line per batch, with the frames it covered
    ## Gateway                               headings as the model wrote them, "##" chapters, "###" sections
    The gateway signs every token.
    {{frame-002: The signing flow}}          a picture line: the frame's number and a one-line caption
    <!-- batch 2 · frames 8-15 -->
    It is refreshed after 50 minutes.        a batch that opens with no heading continues the last section

PHASE 4  finalize, for a direct-draft video run
  actors   program = write_document_node + direct_draft_outline_service, model = phase 4's (`document`), ONE
           conversation of at most video.directDraft.outlineCalls calls
  program  reads    03_draft.md, run-tags.json, 02_frames.json; frames/*.jpg and the video, for the frame tools
  program  sends    the instruction (video/direct_draft_outline.md), the recording's name, the tags and the draft's
                    outline: each section's number, heading, its first line (at most video.directDraft.firstLineChars
                    characters) and the pictures it places; never the sections' text
  model    calls    look, cut, mark, mask, undo_cut (the Quality frame tools, same code) on the frames the pictures
                    name, then submit_outline(title, summary, agenda, changes) once; changes are rename, merge a
                    heading into the one before it, move a heading after another, by section number
  program  applies  the changes; one it cannot do (no such heading) is skipped and named on the console
  program  builds   the document: "# <title>", the executive summary, the agenda (one numbered link line per chapter
                    the model named), the table of contents (every heading, a section under its chapter), then the
                    sections; each picture line becomes the figure of its frame's live cut, a picture line whose
                    frame has no cut is dropped
  program  writes   04_writing/direct-draft-outline.json (the answer, reused by a resume, which then calls no model),
                    04_document.md, the published folder <name>/<name>.md with its figures in resources/ and its
                    provenance.json; logs/fast-turns/fast-document.jsonl, the conversation's turns for the page's
                    worker panel
  a conversation that never submits publishes the draft as it stands, titled with the recording's name
  nothing else of phase 4 runs, and phases 5 and 6 are off

PHASE 3  frames_processing, for a fast-processing video run
  actors   program = process_frames_node + fast_frame_note_service, model = video.fastProcessing.frameModel
           (qwen3.5-4b), one session per video.fastProcessing.frameFollowUps (5) frames, each frame a follow-up
  program  reads    02_frames.json and frames/*.jpg; logs/worker-messages/frames_processing.jsonl, what the person
                                         wrote to the worker, added before the next frame
  program  sends    the instruction once a session, then each frame's number and its image, nothing else
  model    calls    submit_note(description) once per frame; a reply with no call keeps its text; nothing is checked
  program  writes   03_frame-descriptions.md   one section per frame: "## Frame N · mm:ss", the speech said while it
                                         was on screen (from 02_frames.json), the description; appended as each comes
  program  writes   03_processed-frames/frame-NNN.md   the same section, one file per frame, for the page's Frame
                                         notes; no later phase reads it
  program  writes   logs/fast-turns/fast-notes-<first>-<last>.jsonl   each call of the session as a turn: its
                                         thinking, its words and its submit_note; read only by the page's worker panels
  program  writes   each frame's status written in the run store; no notes in 02_frames.json, no 03_notes.md

PHASE 4  finalize, for a fast-processing video run
  actors   program = write_document_node + fast_document_service, model = video.fastProcessing.documentModel
           (`document`), ONE call, thinking as video.fastProcessing.isDocumentThinkingOn
  program  reads    03_frame-descriptions.md, run-tags.json and the ingesting profile's section
  model    writes   the reference document in markdown; nothing is checked
  program  writes   04_writing/fast-document.md (the answer, reused by a resume), 04_document.md and the published
                                         folder <name>/<name>.md with its provenance.json; logs/fast-turns/
                                         fast-document.jsonl, the call's thinking and answer for the page's worker panel
  nothing else of phase 4 runs, and phases 5 and 6 are off

PHASE 4  finalize, for the profile `takeaway`
  actors   program = write_document_node, model = phase 4's (`document` by default), ONE call, with no thinking (audio.takeaway.isThinkingOn)
  program  reads    01_transcript.json and run-tags.json
  program  sends    every transcript line with its id (S0001 on), time and speaker, and the tags
  model    writes   "# <title>", "## Agreed" and "## Not settled", each line one fact ending with its transcript ids
  program  checks and writes   04_writing/takeaway.md   (both sections, a line in them, every line cited with lines of
                                         the transcript)
  program  writes   04_document.md and 04_document.provenance.json   the two lists without the ids; each line's lines
  program  writes   <name>/<name>.md and <name>/provenance.json      the published folder
  nothing else of phase 4 runs, and phases 5 and 6 are off

PHASE 4  finalize
  actors   program = write_document_node, model = phase 4's (`document` by default)
  program  reads    02_frames.json and 01_transcript.json
  program  joins    each frame's notes with the words spoken over it, in memory
  program  reads    ingesting-profile.json and run-tags.json: the profile's file (agent-context/base/) and the tags
                    go into the writer's instructions; a recording with no frames is told so and offered no frame tool
  program  reads    02_speech.md, for an audio recording: each paragraph is one entry under its first line's id,
                    holding its clean text and its speaker, in place of the raw lines
  program  reads    03_prepared/, for an audio recording that phase 3 prepared: each section of a step no later step
                    details is one entry P<step>.<item>.<section>, an item's sections together at its first paragraph;
                    a paragraph one of them cites need not be cited
  program  hands    the meeting's moments to the summary draft and the agenda: one per frame with what it showed and the
                    start of its words; with no frame (an audio recording), one per speech paragraph with its words whole
  program  reads    03_batch_summaries.json
  program  adds     each batch's title, story, atmosphere, questions and answers as a section after the batch's last frame
  program  drops    each frame's own open questions when its batch asked its open questions (03_batch_summaries.json's
                    openQuestions or answeredQuestions): the batch's list, asked with every frame in view, stands for them
  program  reads    03_notes.md          to measure the complete notes; the model never opens this file
  model    reads    the same text, held in memory by the program, one whole chapter per read through its read_notes tool. It has never seen
                    it: phase 3 made it. It is the model's only source for what the meeting said, so 04_document.md is written from it
  program  hands    the batch stories to the model, for the agenda step and its list_batches tool
  model    reads    the frames in frames/ and the video, through its look, cut, mark and mask tools: each chapter places its own figures
  model    writes   in this order, each answer through a tool, and the program saves it:
                    1. the executive summary draft    submit_executive_summary_draft   ─► 04_writing/executive-summary-draft.json
                    2. the agenda                     submit_agenda                    ─► 04_writing/parts.json
                    3. each chapter's headings        submit_draft_headings            ─► 04_writing/draft-headings/*.json
  program  writes   instead of steps 1 to 3, for a PDF read as text only whose chapter level (the top level, or the one under a
                    lone title) holds two headings or more: 04_writing/parts.json, one point a heading of that level, and
                    04_writing/draft-headings/*.json, each chapter's headings of the next level, from 01_transcript.json's
                    `#` lines (no model call; the working title is the document's first heading)
  program  builds   the table of contents from those draft headings (no model call): one chapter per agenda point, its headings under
                    it, each with its start                                                        ─► 04_writing/outlines/table-of-contents.json
                    and the scaffold with a placeholder under every heading                        ─► 04_writing/draft.md
                    4. each chapter's text (in parts  submit_chapter                   ─► 04_writing/sections/*.md
                       of at most 70 notes; each chapter's part count first               ─► 04_writing/chapter-parts.json, read by the page's step table)
                    5. the final summary and title    submit_final                     ─► 04_writing/summary.json
  program  checks   every section before saving it: each entry is used in a block (a miss is a rejection that names the entry)
  program  saves    04_document.md, 04_document.provenance.json (every paragraph with the entries, times and frames it rests on),
                    04_writing/images.json (the looks and cuts the chapters made) and the cut files in 04_writing/images/cuts/
  program  compacts the conversation at its size limit (120,000 tokens), and BEFORE a whole-chapter read that would pass it: the model writes
                    a summary of its own work with the prompt of its worker (7 prompts, agent-context/compaction/conversation/), and the conversation becomes the
                    starting messages, the follow-up and one message with three parts: what the program measured (reads, frame looks and cuts, last
                    answer, sizes), that summary, and what the program checked in it, then the follow-up's last turns word for word
                    (up to 10,000 tokens, opening on a reply of the model); the summary covers only what came before them. A summary that comes back empty twice leaves the follow-up on the
                    measured part alone. Each compaction is one row in 04_writing/logs/conversation.jsonl and one console line
  model    reports  through report_problem, at any turn, a check it believes rejected a right answer, an unclear instruction or a missing
                    tool; every rejection asks it to. The program appends it to worker-feedback.md in the run folder (one entry per problem,
                    a repeat raises its count) and prints it in full on the console
  program  stops    at the first follow-up whose retry budget is spent: nothing after it is asked, no document is written, nothing is
                    published and no review runs; the console prints every rejection with its reasons; the block is a row of
                    04_writing/logs/conversation.jsonl (each row carries its time, `at`), which `i2k blocks` and the detail view count
  program  writes   04_writing/logs/system-prompt.md (the conversation's instructions) and 04_writing/logs/requests/<follow-up>.md
                    (each follow-up's request) as each follow-up opens, for the detail view's "What the worker saw"
  program  writes   04_writing/logs/thinking-live.md, the running call's thinking as it streams, for a monitor to tell a loop from
                    a long draft; no step reads it
  program  publishes  (publication_service, no model) reads 04_document.md, 04_document.provenance.json, 04_writing/images.json and 02_frames.json;
                    keeps every frame a chapter placed, places each of a PDF's `placedFigures` before the first chapter paragraph
                    citing its `placeBefore` paragraph or a later one (04_writing/placed-figures/<frame>.png), then writes
                    <meeting name>/<meeting name>.md, resources/figure-NN.png, provenance.json,
                    the document's metadata block naming the run's tags (run-tags.json); the final answer's tags (at most tags.workerTags,
                    each from the tag inventory or new) are added to run-tags.json first,
                    and checks the folder from disk

PHASE 5  review
  actors   program = review_document_node, model = phase 5's (`document` by default)
  switch   review.isOn (on by default): off, nothing is read or written, the phase is recorded "off", the published
                    document stays as phase 4 wrote it, and the run counts as done at phase 4
  audio    an audio recording's flow has no review: recorded "off" as above, whatever the switch, and no card shown
  program  reads    the published document, its resources/figure-NN.png and provenance.json (each figure's masks and the
                    rectangles they cover), 02_frames.json, the speech and the frames
  program  reads    ingesting-profile.json and run-tags.json, into the reviewer's instructions, as phase 4 does
  program  repairs  before the critique, with no model: removes a figure that repeats an earlier one or that its recorded masks hide past
                    a fifth (pure black counted only for a masked figure published before the rectangles were kept), removes the backticks of a paragraph whose code span was left open, removes a sentence said
                    earlier, repairs a diagram Mermaid cannot draw; kept as `audit` in the review store, listed in review.md
  model    reads    them through its read_frame_record tool and its other reading tools, and lists what a reader still cannot know
  program  writes   05_review/before.md (the document as phase 4 published it), makes each accepted edit in the published document, moves the image of each figure its audit removed into 05_review/removed-figures/ and renames the later images to their renumbered links, recorded in removed-figures/renames.json (a reset to phase 4 undoes both), and writes 05_review/review.md
  model    reports  through report_problem, as in phase 4, into the same worker-feedback.md
  program  writes   05_review/logs/conversation.jsonl, 05_review/logs/system-prompt.md, 05_review/logs/thinking-live.md and 05_review/logs/requests/<follow-up>.md,
                    as phase 4 does

PHASE 6  critique collection
  actors   program = collect_critiques_node, model = phase 6's (`document` by default)
  audio    an audio recording's flow has no critique collection (it writes no critique): recorded "off", nothing read or
           written
  program  reads    03_processed-frames/*-critique.md   every batch's critique of this run
  program  groups   the rules that say the same thing into one anti-pattern, and counts the batches and frames each
                    one hit: the grouping and the counts are the PROGRAM's, never the model's
  model    writes   one rule per group, worded for any meeting; a group that is one meeting's content it leaves out
  program  writes   06_critique-collection/critique-collection.md   the run's rule set, most frequent first, with the
                    program's counts (a run made before the rename has it as 06_lessons/anti-patterns.md, still read)
  program  holds    the phase (status held, never complete) while an earlier phase or any frame is blocked or failed,
                    and when critiqueCollection.isOn is off: a rule learned from a run with a known hole teaches the hole
  person   reads    it into agent-context/instructions/frames_processing.md, which is the only reason the phase exists

PHASE 7  document reshaping: clean up, rewrite or knowledge reshaping (off unless the run asks for it); its folder is
         07_reshaping/, or 07_cleanup/ in a run made before the rename
  actors   program = reshape_document_node, model = phase 7's (`document` by default)
  asked    by run-document-pass.json ({"pass": "cleanup" | "rewrite" | "knowledge", "wishes": ...}), which
           `run --document-pass` and `--document-wishes`, the run form's PDF Document "Markdown + LLM clean up /
           rewrite / knowledge" and a done PDF run's Change this document write; without it the phase is recorded "off"
  program  reads    the published document <name>/<name>.md, held while phase 4 published none
  program  writes   07_reshaping/before.md (the document before the pass) and 07_reshaping/state.json: the document cut into
                    numbered blocks (a heading, a code block, a paragraph), the blocks into batches of at least
                    documentReshaping.batchTokens (30,000) estimated tokens, each ending before a heading; before
                    each part the parts not started are cut again at the size the settings hold then
  program  writes   after every turn 07_reshaping/conversations/batch-NNN.json (the part's conversation and what it
                    read), which a resume of a part stopped mid-way goes on with
  person   writes   while a part runs, from its live panel, 07_reshaping/messages/part-NNN.jsonl (a question, or a message
                    as user or system), which the program adds to the conversation before the part's next call
  model    reads    per batch, in a conversation of its own: the document's size, tokens and figure count, its outline
                    (read_outline) and its batch's blocks (read_blocks; a read past one batch is refused)
  model    edits    clean up and rewrite: its batch's blocks through edit_blocks (remove, replace, insert_after; a
                    replace may not drop a figure line), then finish_batch once every block of it was read
  model    writes   knowledge: is handed its batch's blocks in its first message, reads the knowledge outline so far
                    (read_knowledge_outline), a section before changing it (read_section) and a figure's image
                    (look_figure), and writes the facts into one section per subject through write_sections (new_section,
                    add, replace, remove, rename, remove_section, merge, split, move; a section left with no block is
                    taken out), kept in 07_reshaping/state.json beside the blocks; then finish_batch
  program  writes   before each batch's first call 07_reshaping/system-prompt.md and 07_reshaping/requests/batch-NNN.md
                    (what its worker is sent, which What the worker saw shows)
  program  writes   the published document after every edit (knowledge: the title and the sections), 07_reshaping/
                    batch-NNN.jsonl (each turn, read with state.json for the phase's bar of parts), 07_reshaping/
                    thinking/part-NNN.md (the thinking of the part's model call under way, as it streams) and
                    thinking/part-NNN.json (when that call started, its first piece and, once it ended, its tokens and
                    speed), which the part's live panel shows, for a knowledge pass 07_reshaping/parts/part-NNN.md
                    (the document as each part left it, written after every turn, which the Open document menu lists
                    as that part's draft and the part's row opens), and
                    07_reshaping/report.md (each batch's summary, the size before and after) last
  program  holds    nothing else: a resume of a finished pass leaves the document alone; a pass asked again moves the
                    finished one into 07_reshaping/earlier/<n>/ and runs over the document as it is now; reset-to 6 puts
                    the document back from the first pass's before.md

END OF THE RUN
  program  moves    when the run record holds every phase complete and the recording is an inbox video: the video (or its
                    meeting folder) into <run folder>/recording/, with recording/.origin.json naming its inbox path
                    (run_recording_service); phases 1 and 2 then wait until `recording-to-inbox` puts it back
  program  moves    with the run record holding every phase complete: every cut file in 04_writing/images/cuts/ that no
                    section's markdown and no block of 04_writing/images.json names, into <run folder>/.trash/cuts/,
                    each move recorded in the store's removedUnreferenced and so in the published provenance
                    (cut_tidy_service); `i2k tidy-cuts` does the same for a run folder that finished earlier

CONVERT TO ANOTHER INGESTING PROFILE
  actors   program = profile_convert_service (`i2k convert --output <run> --to <profile>`, the page's Convert), no model
  program  reads    the run folder, only: nothing in it is changed
  program  writes   <run>-<profile>/   the numbered files of phases 1 to 3 (01_*, 02_*, 03_*, 03_processed-frames/,
                                       03_prepared/), frames/ and run-tags.json, by hard link where the disk allows, and
                                       ingesting-profile.json with chosenBy convert; refused when that folder exists;
                                       without 03_* when the two profiles prepare an audio recording in other steps
  program  queues   (the page) `run --from 4 --force` there, or `--from 3` when phase 3 prepares again: the later
                    phases write the document again in the other profile; the CLI prints that line

WRITE A FINISHED AUDIO RUN AGAIN IN ANOTHER PROFILE, IN THE SAME FOLDER
  actors   program = profile_version_service (`i2k convert ... --in-place`, the page's Change this document), no model;
           only a profile whose phase 3 prepares the notes as the run's own does (select_first_phase is 4), never takeaway
  program  moves    into 04_versions/<n>/: 04_document.md, 04_document.provenance.json, 04_writing/, phase 4's call logs,
                    the published folder, 07_reshaping/, run-document-pass.json and ingesting-profile.json, and writes
                    version.json there (the profile, the published document's path); nothing is deleted
  program  forgets  phase 4 and later in the run stores, as reset-to 3 does, and writes the new ingesting-profile.json
                    with chosenBy convert
  program  queues   (the page) `run --from 4 --force` on the same folder; the CLI prints that line
  reader            the Open document menu lists each 04_versions/<n>/ document by its profile, then the current one
```

For a PDF phase 2 renders every page as frames/frame-NNN.jpg, 1,600 pixels wide, keeps every one (no classifier, board
stage or model filter runs), and gives each its own page's text as what is said over it and the page before as what
came before, and marks a page with an earlier page's text and look `repeatOf` that page; `02_frames.json` holds `"medium": "pdf"`, and every item `medium` and `page`, which add
agent-context/base/medium-pdf.md to phase 3's instructions. Phase 4's cut renders the page again at 200 DPI.

A PDF read as text only (`run --pdf-mode text-only`, `run-medium.json` holding `pdf-text-only`; `pdf-markdown` in runs
made before the rename) takes no page as a frame:
phase 1 also writes into `01_transcript.json` the document's `figures` (each picture or drawing with its page, its box,
its caption and the `start` just before its caption's block; `pdf_source_service.find_figures`), a figure the page
break cut in two written once, on its first page, with `parts` naming each page's box;
phase 2 writes `02_speech.md`, titled Paragraphs, one paragraph per block of `01_transcript.json` under `## S<n>` with no
speaker and no model call, and `02_frames.json` with `"medium": "pdf-text-only"` and one item per figure, cut from its page into
frames/frame-NNN.jpg (`"medium": "pdf-figure"`, its caption as what is said over it, the page's text before it as what
came before, its `box` on the page at 200 DPI, which the cut tool reads, and for a figure over a page break `parts`, each
page's box at 200 DPI, its frame and its cuts the parts stacked, and its `kind`, picture or drawing); the figures
`pdf.figureReading` leaves unread (every one above `pdf.figureReadLimit` under "place-above-limit", the default, or the
pictures under "read-drawings-only") stand apart under `placedFigures` with `placeBefore`, the id of the paragraph that
followed each, and no phase 3 or 4 step reads them; phase 3 reads the figure frames of `items` as a
video's, with agent-context/base/medium-pdf-figure.md, and is recorded complete with nothing read when there is none;
phases 4 to 6 read the paragraphs as an audio recording's, its moments the paragraphs and the figures together, with
the frame tools when there is a figure (phases 5 and 6 `off`).

A PDF's markdown conversion (`run --pdf-mode markdown-conversion`, `run-medium.json` holding `pdf-markdown-conversion`;
`pdf-no-model` in runs made before the rename) runs phases 1 and 2 as a PDF read as text only, except that phase 2
places every figure under `placedFigures` and writes `02_frames.json` with `"medium": "pdf-markdown-conversion"` and no
item; phase 3 is recorded complete with nothing read; phase 4 makes no model call:
`pdf_conversion_service` writes `04_document.md` from the paragraphs of `02_speech.md` as they stand, each figure before
the paragraph its `placeBefore` names, `04_document.provenance.json` (each paragraph with its ids), and the published
folder `<name>/<name>.md` with `resources/figure-NN.png` and `provenance.json`; phases 5 and 6 are `off`, and phase 7
runs when the run asked for an LLM clean up, rewrite or knowledge pass.

A Word (`.docx`) or OpenDocument text (`.odt`) file, the office workflow, is a markdown
conversion unless the run asks for text only (`run-medium.json` holding `pdf-markdown-conversion`, written before phase 1;
"pages as images" and a range are refused). Phase 1 reads the file with no speech recognition: an `.odt` is first turned
into `.docx` by LibreOffice when it is installed (`01_office-media-docx/`), which keeps merged table cells; pandoc
writes the markdown (`01_office-media.md`) with tools/pandoc/merged_tables_as_html.lua (a table with a merged cell as
HTML) and every picture under `01_office-media/`, an `.emf` or `.wmf` turned into PNG by LibreOffice (or left out and
named); the document's own table of contents is taken out into `01_cleanup.json`; `01_transcript.json` holds one
segment per block, a page of the timeline every 20 blocks, and `figures` naming each picture's file (`image`), its
caption (a "Figure N" block after it) and its `start`. Phase 2 writes each picture as frames/frame-NNN.jpg
(`office_source_service.render_figures`, an SVG drawn by rsvg-convert) and goes on as a PDF's markdown conversion.

A slide deck (`.pptx`, `.odp`), the PPT workflow, is always a PDF read as pages as images (any
other mode and a range are refused). Phase 1 makes no model call: LibreOffice draws the deck into `01_slides.pdf` (hidden
slides left out, each named in `01_cleanup.json`), the program reads each shown slide's speaker notes from the deck's own
XML and refuses a PDF whose page count is not the shown slides', and `01_transcript.json` holds one segment per slide with
notes, at its page's minute, the notes as its text, and `slides` naming each slide's page: the slide's own words are
never what is said over it, so the model reads the slide from its picture alone. Phase 2 renders `01_slides.pdf`'s pages
as a PDF's (`frame_preparation_service._prepare_pdf_pages`), and everything after is a PDF of pages; the cut tool cuts a
slide from its stored frame.

Every model call of every phase reads its instructions at the moment it is made: each file of `agent-context/` (the
instructions, the shared guidelines, the documents its rules add, the compaction texts) as a person edited it on the
page, from the workspace's `instructions/edits/`, else as the program ships it. The page writes `instructions/edits/`,
`instructions/history/` and `instructions/defaults/` there; no phase writes them.

Read by no later step: 01_cleanup.json, the frame and batch files in `03_processed-frames/`, the batch atmosphere, the speech copied into
`02_frames.json`, its drop scores, its `textOnlyScore`s and `droppedFrames` with `frames/dropped/` (read only by the detail view), `worker-feedback.md`, which is for the people building the program, the conversations' `logs/system-prompt.md` and `logs/requests/` (read only by the detail view's "What the worker saw"), and `run-name.json`, the name the Manager's Rename shows the run under. The `-critique.md` files there are read
by phase 6, and `06_critique-collection/critique-collection.md` is read by a person, into the worker's instructions.

A run is finished once phases 1 to 5 are complete: phase 6 writes rules for this program's own instructions, not part of the
meeting's output, so a run whose phase 6 is off or held is a finished run (`run_recording_service.is_run_done`).
