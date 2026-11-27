"""Phase 7, document reshaping: the document's clean up, rewrite or knowledge pass, one worker editing the published
document, or writing its facts into one section per subject, a part at a time (`services/document_reshaping_service.py`,
`services/document_knowledge_service.py`).

Off unless the run asked for it (`run --document-pass`, the run form's PDF Document list, the detail view's Change this
document). Held while phase 4 has published no document. A pass already run to its report is not run again until it is
asked again. A batch not closed within its calls blocks the phase, its edits kept; a resume starts at that batch.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from agent_client.agent_errors import ControlStop
from graph.graph_state import RunState
from models import output_contract
from models.run_context import RunContext
from services import chat_model_service
from services import control_signal_service
from services import document_reshaping_service
from services import document_folder_service
from services import document_knowledge_service
from services import document_pass_service
from services import performance_log_service

PHASE = output_contract.DOCUMENT_RESHAPING_PHASE
PHASE_NUMBER = output_contract.PHASE_NUMBERS[PHASE]


def _mark_phase(ctx: RunContext,
                status: str,
                handles) -> dict:
    performance_log_service.record_phase_end(ctx, PHASE, status, handles)

    return {"phases": {PHASE: status}}


def reshape_document_node(state: RunState,
                        config: RunnableConfig) -> dict:
    ctx: RunContext = config["configurable"]["ctx"]
    output_root = ctx.paths.output_root

    # STEP 1: off unless asked, held without a published document, complete when this request already ran
    asked = document_pass_service.read_document_pass(output_root)
    if asked is None:
        ctx.log.info(f"phase {PHASE_NUMBER}: off, the run asked for no clean up or rewrite")

        return _mark_phase(ctx, "off", None)
    title = document_pass_service.describe_pass(asked["pass"])
    if not ctx.paths.summary.is_file() or not document_folder_service.build_document_path(ctx.paths).is_file():
        ctx.log.info(f"phase {PHASE_NUMBER}: not started, phase 4 has published no document")

        return {"phases": {PHASE: "held"}}
    if document_pass_service.is_pass_done(output_root):
        ctx.log.info(f"phase {PHASE_NUMBER}: the {title} asked for already ran; ask for it again to run another pass")

        return _mark_phase(ctx, "complete", None)
    if state.get("is_dry_run"):
        ctx.log.info(f"phase {PHASE_NUMBER}: dry run, the document would be given a {title} pass; no model is called")

        return {"phases": {PHASE: "complete"}}

    # STEP 2: the blocks and batches, kept from an earlier start of the same request; the knowledge pass writes a new
    # document beside them, the clean up and the rewrite edit the blocks in place
    handles = performance_log_service.start_unit(ctx, PHASE, None)
    is_knowledge = asked["pass"] == output_contract.DOCUMENT_PASS_KNOWLEDGE
    pass_service = document_knowledge_service if is_knowledge else document_reshaping_service
    if is_knowledge:
        pass_state = document_knowledge_service.start_knowledge(paths=ctx.paths, asked=asked)
    else:
        pass_state = document_reshaping_service.start_pass(paths=ctx.paths, asked=asked)
    size = pass_state["size"]
    ctx.log.info(f"phase {PHASE_NUMBER}: {title} of a document of about {size['tokens']:,} tokens, "
                 f"{size['blocks']:,} blocks and {size['figures']} figures, in {len(pass_state['batches'])} batch(es); "
                 f"{len(pass_state['closed'])} closed before")

    # STEP 3: each batch not closed yet, its own conversation; one not closed blocks the phase. Before each, the parts
    # no worker has started are cut again at the part size the settings hold now, so a size saved while the pass runs
    # reaches it at the next part, the part under way kept whole
    model = ctx.model if ctx.model is not None else chat_model_service.get_model(PHASE)
    while document_reshaping_service.list_open_batches(pass_state):
        part_tokens = document_reshaping_service.read_part_tokens()
        if document_reshaping_service.split_open_parts(ctx.paths, pass_state, part_tokens):
            document_reshaping_service.write_state(paths=ctx.paths, state=pass_state)
            ctx.log.info(f"phase {PHASE_NUMBER}: the parts not started are cut again at {part_tokens:,} tokens, "
                         f"{len(pass_state['batches'])} parts now")
        number = document_reshaping_service.list_open_batches(pass_state)[0]
        # Drain finishes the part under way and stops before the next; Stop ends a part between two of its calls
        if ctx.control_check is not None and ctx.control_check() in (control_signal_service.DRAIN,
                                                                     control_signal_service.STOP):
            raise ControlStop(f"stopped before part {number} of phase {PHASE_NUMBER}")
        is_closed, calls, reason = pass_service.run_batch(paths=ctx.paths, state=pass_state, number=number, model=model,
                                                          log=ctx.log, control_check=ctx.control_check)
        if not is_closed:
            ctx.log.info(f"phase {PHASE_NUMBER}: blocked, {reason}")

            return _mark_phase(ctx, "blocked", handles)
        ctx.log.info(f"phase {PHASE_NUMBER}: batch {number} of {len(pass_state['batches'])} closed in {calls} call(s)")

    report = pass_service.write_report(paths=ctx.paths, state=pass_state)
    ctx.log.info(f"phase {PHASE_NUMBER}: {report.name} written")

    return _mark_phase(ctx, "complete", handles)
