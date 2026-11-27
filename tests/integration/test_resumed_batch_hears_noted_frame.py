"""A resumed batch that holds a frame asked again and the frames after it, with a frame between them accepted in an
earlier batch, through the real batch description and the real answers message, on a made run of four frames: the
answers step hears the accepted frame's words, where the speaker's answer was."""

from support import frame_batch_fixtures
from support import harness

from graph.nodes import process_frames_node
from models.attempt_request import AttemptRequest
from prompts import frames_processing_messages
from services.ignore_list_service import IgnoreList


def _all_text(messages) -> str:
    return "\n".join(str(part.get("text", "")) for message in messages
                     for part in (message.content if isinstance(message.content, list) else [{"text": message.content}])
                     if isinstance(part, dict))


def test_the_answers_step_hears_the_frame_noted_in_an_earlier_batch_defect():
    # A talk's question was answered over frame 20, accepted in an earlier batch; the resumed batch (19, 21 and on)
    # never heard it and kept the question open. Now its follow-ups hear every frame between its first and last
    ctx = frame_batch_fixtures.load_run_context(count=4)
    ctx.store.get_item("frame-001")["transcriptCurrent"] = "Jonas asks how big a chunk is. Like if I were to"
    ctx.store.get_item("frame-002")["transcriptCurrent"] = "simplify it, AcmeSync cuts every file into four megabyte chunks."
    ctx.store.get_item("frame-003")["transcriptCurrent"] = "So, the next step is the upload."
    covered = [ctx.store.get_item("frame-001"), ctx.store.get_item("frame-003")]
    batch = process_frames_node._describe_batch(ctx, covered, "", "")
    batch["questions"] = ["How big is a chunk?"]
    request = AttemptRequest(job_kind="answer_questions", attempt=1, repository=ctx.paths.repository,
                             module=ctx.paths.module, paths=ctx.paths, ignore=IgnoreList(),
                             inputs={"slug": "frames-001-003", "batch": batch})
    text = _all_text(frames_processing_messages.build_answer_questions_messages(request))
    harness.check("the answers step hears frame 2's words", "four megabyte chunks" in text, text[:1200])
    harness.check("frame 2 is named as noted in an earlier batch", "Frame 2 (at" in text and "noted in an earlier batch" in text,
                  text[:1200])
