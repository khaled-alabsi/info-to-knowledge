"""A knowledge pass started on a made document (every word invented) whose long middle stretch has no heading, as a real
export's did: part 14 of a knowledge pass held 1,221 blocks, about 265,000 tokens, and its first call was refused by a
model loaded with 183,040. Run through `document_knowledge_service.start_knowledge` and the parts' recut before a part
(`document_reshaping_service.split_open_parts`), with the settings' own part size."""

import tempfile
from pathlib import Path

from support import harness

import config as app_config
from models.run_paths import RunPaths
from services import document_folder_service
from services import document_knowledge_service
from services import document_reshaping_service

# A paragraph of the stretch: about 400 characters of invented ledger rules
PARAGRAPH = ("The ledger shard of a tenant is picked from its id, and a posting written to the wrong shard is moved back "
             "by the nightly check, which writes each move to the audit list. ") * 3


def _build_document(paragraphs: int) -> str:
    stretch = "\n\n".join(f"{index}. {PARAGRAPH}" for index in range(paragraphs))

    return f"# Gateway guide\n\n## Ledger rules\n\n{stretch}\n\n## Refunds\n\nRefunds are paid within a day.\n"


def test_a_long_stretch_with_no_heading_is_split_into_parts_a_model_can_take_defect():
    # Defect: a part ended only before a heading, so a stretch of 1,221 blocks with no heading was one part of about
    # 265,000 tokens, refused by the model, and the run ended. Fix: a part is cut at the next block past twice its size
    folder = Path(tempfile.mkdtemp())
    paths = RunPaths(repository=folder, module=folder.name, output_root_override=folder)
    settings = app_config.settings.document_reshaping
    part_chars = settings.batch_tokens * settings.chars_per_token
    document = _build_document(paragraphs=int(part_chars * 6 / len(PARAGRAPH)))
    paths.summary.parent.mkdir(parents=True, exist_ok=True)
    paths.summary.write_text(document, encoding="utf-8")
    published = document_folder_service.build_document_path(paths)
    published.parent.mkdir(parents=True)
    published.write_text(document, encoding="utf-8")

    state = document_knowledge_service.start_knowledge(paths=paths, asked={"pass": "knowledge", "wishes": ""})
    document_reshaping_service.split_open_parts(paths, state, settings.batch_tokens)
    by_id = {block["id"]: block for block in state["blocks"]}
    tokens = [round(sum(len(by_id[block_id]["text"]) for block_id in batch) / settings.chars_per_token)
              for batch in state["batches"]]
    harness.check("the stretch is several parts, none past twice the part size and one paragraph",
                  len(tokens) >= 3 and max(tokens) <= 2 * settings.batch_tokens + len(PARAGRAPH), str(tokens))
    harness.check("every block is in one part, in order",
                  [block_id for batch in state["batches"] for block_id in batch] == sorted(by_id), "")
