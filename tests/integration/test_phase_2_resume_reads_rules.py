"""Phase 2 on a video rendered from HTML pages (`tests/resources/scenes/loop-slide-table.mp4`, by its `render.sh`), the
real capture loop taking its frames, then resumed in the same run: release test 2's run was stopped by a reboot after
the rules had taken its frames in 20 minutes, and its resume took them all again. Now the same run reads them back."""

import logging
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

from support import harness

import config
from models.run_paths import RunPaths
from services import frame_preparation_service
from services import phase_file_service
from services import run_paths_service

SCENES = Path(__file__).resolve().parents[1] / "resources" / "scenes"


def _build_context(video: Path) -> SimpleNamespace:
    paths = RunPaths(repository=video.parent, module=video.name, output_root_override=video.parent / "out")
    run_paths_service.ensure_run_paths(paths)
    segments = [{"text": " eine Tabelle", "start": 0.0, "end": 4.0,
                 "words": [{"word": " eine", "start": 0.5, "end": 0.9}, {"word": " Tabelle", "start": 1.0, "end": 1.6}]}]
    phase_file_service.write_phase_file(paths, 1, {"schemaVersion": 1, "hasAudio": True, "language": "de",
                                                   "text": "eine Tabelle", "segments": segments})
    run_dir = paths.output_root / "logs" / "2026-10-30_13-38"
    run_dir.mkdir(parents=True, exist_ok=True)

    return SimpleNamespace(repository=paths.repository, module=paths.module, paths=paths, run_dir=run_dir,
                           log=logging.getLogger("test"))


def _prepare(ctx: SimpleNamespace) -> dict:
    frames = config.settings.frames
    names = ("selection_mode", "is_knowledge_filter_on", "is_text_only_mark_on", "is_board_stage_on",
             "is_redundancy_filter_on", "is_similarity_recheck_on")
    previous = [getattr(frames, name) for name in names]
    for name, value in zip(names, ("change", False, False, False, False, False)):
        setattr(frames, name, value)
    try:
        return frame_preparation_service.prepare_frames(ctx)
    finally:
        for name, value in zip(names, previous):
            setattr(frames, name, value)


def test_a_resumed_phase_2_reads_the_frames_the_capture_loop_took_defect():
    # The resume of a run stopped after phase 2's rules took every frame again. Now it reads them back
    video = SCENES / "loop-slide-table.mp4"
    if not video.is_file():
        print(f"scene video not built: run {SCENES / 'render.sh'} (needs Chrome and ffmpeg): skipped")
        return
    folder = Path(tempfile.mkdtemp())
    ctx = _build_context(Path(shutil.copy(video, folder / video.name)))
    first = _prepare(ctx)
    extract = frame_preparation_service.frame_extraction_service.extract_frames
    taken = []
    frame_preparation_service.frame_extraction_service.extract_frames = lambda *args: taken.append(1) or extract(*args)
    try:
        again = _prepare(ctx)
    finally:
        frame_preparation_service.frame_extraction_service.extract_frames = extract
    harness.check("the capture loop took frames", len(first["items"]) >= 2, str(len(first["items"])))
    harness.check("the resume takes none again", taken == [], str(taken))
    harness.check("the resume keeps the same frames with the same boxes",
                  [(item["slug"], item["box"]) for item in again["items"]]
                  == [(item["slug"], item["box"]) for item in first["items"]], str(again["items"])[:600])
