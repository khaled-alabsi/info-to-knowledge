"""Assembly. Nothing else lives here.

Open this file and the whole run should be readable. Belongs here: nodes, edges, conditional edges,
subgraphs, compile configuration. Does not belong here: prompts, file parsing, business logic,
Pydantic definitions, provider handling.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from graph import routing
from graph.graph_state import RunState
from graph.nodes import prepare_frames_node
from graph.nodes import reshape_document_node
from graph.nodes import collect_critiques_node
from graph.nodes import review_document_node
from graph.nodes import prepare_notes_node
from graph.nodes import prepare_speech_node
from graph.nodes import transcribe_audio_node
from graph.nodes import process_frames_node
from graph.nodes import write_document_node
from models.run_context import RunContext

__all__ = ["build_graph"]


def build_graph(ctx: RunContext):
    """A factory, not a module-level singleton: the context carries a live store, which is not state
    and may not be checkpointed. No node takes a factory; every one reads `ctx` off the config bound
    here."""

    builder = StateGraph(RunState)

    builder.add_node("transcribe", transcribe_audio_node.produce_transcript_node)        # phase 1, plain code: 01_transcript.json
    builder.add_node("frames_preparing", prepare_frames_node.prepare_frames_node)        # phase 2, plain code: 02_frames.json
    builder.add_node("speech_structuring", prepare_speech_node.prepare_speech_node)      # phase 2 of an audio recording, one call: 02_speech.md
    builder.add_node("frames_processing", process_frames_node.process_frames_node)       # phase 3, one model call per batch of frames
    builder.add_node("prepare_notes", prepare_notes_node.prepare_notes_node)             # phase 3 of an audio recording, a call per step or item: 03_prepared/
    builder.add_node("finalize", write_document_node.write_document_node)                # phase 4, the document, then its published folder
    builder.add_node("review", review_document_node.write_reviewed_document_node)        # phase 5, a critic, then its todos
    builder.add_node("critique_collection", collect_critiques_node.collect_critiques_node)  # phase 6, the critiques as one rule set
    builder.add_node("document_reshaping", reshape_document_node.reshape_document_node)    # phase 7, off unless asked: the document reshaped (cleaned up, rewritten or as knowledge)

    builder.add_edge(START, "transcribe")
    builder.add_conditional_edges("transcribe", routing.route_after_transcribe,
                                  {"frames_preparing": "frames_preparing", "speech_structuring": "speech_structuring",
                                   "failed": END})
    builder.add_conditional_edges("speech_structuring", routing.route_after_speech_structuring,
                                  {"frames_processing": "frames_processing", "prepare_notes": "prepare_notes",
                                   "finalize": "finalize", "review": "review",
                                   "critique_collection": "critique_collection", "document_reshaping": "document_reshaping",
                                   "done": END, "failed": END})
    builder.add_conditional_edges("prepare_notes", routing.route_after_prepare_notes,
                                  {"finalize": "finalize", "review": "review",
                                   "critique_collection": "critique_collection", "document_reshaping": "document_reshaping",
                                   "done": END})
    builder.add_conditional_edges("frames_preparing", routing.route_after_frames_preparing,
                                  {"frames_processing": "frames_processing", "finalize": "finalize",
                                   "review": "review", "critique_collection": "critique_collection",
                                   "document_reshaping": "document_reshaping", "done": END, "failed": END})
    builder.add_conditional_edges("frames_processing", routing.route_after_frames_processing,
                                  {"frames_processing": "frames_processing", "finalize": "finalize",
                                   "review": "review", "critique_collection": "critique_collection",
                                   "document_reshaping": "document_reshaping", "done": END})
    builder.add_conditional_edges("finalize", routing.route_after_finalize,
                                  {"review": "review", "critique_collection": "critique_collection",
                                   "document_reshaping": "document_reshaping", "done": END, "failed": END})
    builder.add_conditional_edges("review", routing.route_after_review,
                                  {"critique_collection": "critique_collection", "document_reshaping": "document_reshaping",
                                   "done": END})
    builder.add_conditional_edges("critique_collection", routing.route_after_critique_collection,
                                  {"document_reshaping": "document_reshaping", "done": END})
    builder.add_edge("document_reshaping", END)

    return builder.compile().with_config({"configurable": {"ctx": ctx}})

