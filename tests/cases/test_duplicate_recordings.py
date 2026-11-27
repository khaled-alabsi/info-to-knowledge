"""A recording already processed is found by its SHA-256: `run` refuses it unless `--force`, and an upload into the inbox
is refused with the done run it duplicates."""

from __future__ import annotations

import hashlib
import io
import tempfile
import json
from pathlib import Path

from support import fixtures as fx
from support import harness

import config
from cli import run_command
from models.input_range_spec import InputRangeSpec
from models.run_identity import RunIdentity
from models.run_paths import RunPaths
from models.ui_errors import DuplicateRecordingError, UploadRefusedError
from services import inbox_merge_service
from services import input_range_service
from services import recording_hash_service
from services import recording_parts_service
from services import run_status_service
from services import run_store_service
from services import upload_bundle_service
from services import upload_service



def _build_done_run(root: Path,
                    video_bytes: bytes) -> tuple[Path, Path, Path]:
    """An inbox holding one recording and a done run folder under `output/` naming it."""

    inbox, output = root / "inbox", root / "output"
    inbox.mkdir()
    video = inbox / "AcmeSync-review.mp4"
    video.write_bytes(video_bytes)
    folder = output / "AcmeSync-review"
    paths = RunPaths(repository=inbox, module=video.name, output_root_override=folder)
    store = run_store_service.RunStore.create(paths.run_state("2026-10-01_09-00"),
                                              RunIdentity.for_run("2026-10-01_09-00", paths), budgets={}, models=[])
    store.data.update({"status": "complete", "phases": fx.DONE_PHASES})
    store.save()

    return inbox, output, folder


class _Swap:
    """The run roots, the inbox and the hash cache pointed at a test's own folders, put back afterwards."""

    def __init__(self, root: Path, inbox: Path, output: Path):
        self.values = (root, inbox, output)

    def __enter__(self):
        root, inbox, output = self.values
        self.saved = (dict(run_status_service.RUN_ROOTS), config.INBOX_DIR, recording_hash_service.CACHE_PATH)
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update({"output": output})
        config.INBOX_DIR = inbox
        recording_hash_service.set_cache_path(root / "recording-hashes.json")

    def __exit__(self, *exc):
        roots, inbox, cache = self.saved
        run_status_service.RUN_ROOTS.clear()
        run_status_service.RUN_ROOTS.update(roots)
        config.INBOX_DIR = inbox
        recording_hash_service.set_cache_path(cache)


def test_a_recording_s_hash_is_its_sha256_and_is_read_from_the_cache_while_the_file_is_unchanged():
    root = Path(tempfile.mkdtemp())
    video = root / "AcmeSync.mp4"
    video.write_bytes(b"<video bytes>")
    with _Swap(root, root, root):
        first = recording_hash_service.compute_recording_hash(video)
        cached = recording_hash_service.compute_recording_hash(video)
    harness.check("the file's SHA-256", first == hashlib.sha256(b"<video bytes>").hexdigest(), first)
    harness.check("the same again from the cache", cached == first and (root / "recording-hashes.json").is_file(), cached)


def test_a_done_run_is_found_by_its_recording_s_hash_and_its_own_folder_is_left_out():
    root = Path(tempfile.mkdtemp())
    inbox, output, folder = _build_done_run(root, b"<video bytes>")
    with _Swap(root, inbox, output):
        sha = hashlib.sha256(b"<video bytes>").hexdigest()
        found = recording_hash_service.find_done_run(sha)
        own = recording_hash_service.find_done_run(sha, excluded=folder)
        other = recording_hash_service.find_done_run(hashlib.sha256(b"<other>").hexdigest())
    harness.check("found as output/AcmeSync-review", found == {"root": "output", "name": "AcmeSync-review"}, str(found))
    harness.check("its own folder is no duplicate of itself", own is None, str(own))
    harness.check("another recording matches nothing", other is None, str(other))


def test_run_refuses_a_recording_a_done_run_processed_unless_forced():
    root = Path(tempfile.mkdtemp())
    inbox, output, _ = _build_done_run(root, b"<video bytes>")
    copy = inbox / "AcmeSync-review-copy.mp4"
    copy.write_bytes(b"<video bytes>")
    with _Swap(root, inbox, output):
        refusal = run_command.find_duplicate_refusal(copy, output / "AcmeSync-review-copy", is_forced=False)
        forced = run_command.find_duplicate_refusal(copy, output / "AcmeSync-review-copy", is_forced=True)
    harness.check("refused, naming the done run and --force", refusal is not None and "output/AcmeSync-review" in refusal
                  and "--force" in refusal, str(refusal))
    harness.check("--force lets it run", forced is None, str(forced))


def test_another_part_of_a_processed_recording_is_a_new_run_and_the_same_part_is_refused():
    # The recording's hash alone refused a second part of the same recording as a duplicate
    root = Path(tempfile.mkdtemp())
    inbox, output, folder = _build_done_run(root, b"<video bytes>")
    input_range_service.write_range(folder=folder, input_range=InputRangeSpec(start_s=0.0, end_s=600.0))
    copy = inbox / "AcmeSync-review-copy.mp4"
    copy.write_bytes(b"<video bytes>")
    target = output / "AcmeSync-review-copy"
    with _Swap(root, inbox, output):
        whole = run_command.find_duplicate_refusal(copy, target, is_forced=False)
        later = run_command.find_duplicate_refusal(copy, target, is_forced=False,
                                                   input_range=InputRangeSpec(start_s=600.0))
        same = run_command.find_duplicate_refusal(copy, target, is_forced=False,
                                                  input_range=InputRangeSpec(start_s=0.0, end_s=600.0))
    harness.check("the whole recording is not what the done run processed", whole is None, str(whole))
    harness.check("another part runs", later is None, str(later))
    harness.check("the same part is refused, naming the part", same is not None and "0:00 to 10:00" in same, str(same))


def test_an_upload_of_a_processed_recording_is_refused_with_the_run_it_duplicates_and_nothing_is_left():
    root = Path(tempfile.mkdtemp())
    inbox, output, _ = _build_done_run(root, b"<video bytes>")
    with _Swap(root, inbox, output):
        try:
            upload_service.save_upload(io.BytesIO(b"<video bytes>"), inbox, "renamed-copy.mp4")
            harness.check("refused as a duplicate", False, "saved")
        except DuplicateRecordingError as exc:
            harness.check("refused as a duplicate, naming the run", exc.duplicate["run"] == {"root": "output", "name": "AcmeSync-review"}
                          and "output/AcmeSync-review" in str(exc), str(exc))
        saved = upload_service.save_upload(io.BytesIO(b"<another recording>"), inbox, "Jonas-weekly.mp4")
    harness.check("nothing of the refused upload is left in the inbox",
                  sorted(path.name for path in inbox.iterdir()) == ["AcmeSync-review.mp4", "Jonas-weekly.mp4"],
                  str(sorted(path.name for path in inbox.iterdir())))
    harness.check("another recording is saved", saved == inbox / "Jonas-weekly.mp4", str(saved))


def test_a_folder_upload_saves_the_new_videos_and_names_the_processed_one_with_its_run():
    root = Path(tempfile.mkdtemp())
    inbox, output, _ = _build_done_run(root, b"<video bytes>")
    with _Swap(root, inbox, output):
        saved = upload_bundle_service.save_upload_bundle(inbox, [(io.BytesIO(b"<video bytes>"), "drop/part-copy.mp4"),
                                                                 (io.BytesIO(b"<new video>"), "drop/Jonas-weekly.mp4")])
        every = upload_bundle_service.save_upload_bundle(inbox, [(io.BytesIO(b"<video bytes>"), "drop/part-copy.mp4")])
    harness.check("the new video is saved, the processed one refused with its run",
                  saved["added"] == ["Jonas-weekly.mp4"] and (inbox / "Jonas-weekly.mp4").is_file()
                  and saved["refused"][0]["duplicate_of"] == {"root": "output", "name": "AcmeSync-review"}, str(saved))
    harness.check("an upload of processed videos only saves nothing", every["added"] == []
                  and len(every["refused"]) == 1, str(every))


def test_an_upload_of_a_recording_already_waiting_in_the_inbox_is_refused_naming_it():
    root = Path(tempfile.mkdtemp())
    inbox, output = root / "inbox", root / "output"
    inbox.mkdir()
    output.mkdir()
    (inbox / "Jonas-weekly.mp4").write_bytes(b"<waiting video>")
    with _Swap(root, inbox, output):
        try:
            upload_service.save_upload(io.BytesIO(b"<waiting video>"), inbox, "Jonas-weekly.mp4")
            harness.check("refused: the inbox holds it", False, "saved")
        except DuplicateRecordingError as exc:
            harness.check("refused: the inbox holds it, named, no run to open",
                          exc.duplicate["run"] is None and exc.duplicate["kind"] == "in_inbox"
                          and exc.duplicate["same_as"] == "Jonas-weekly.mp4", str(exc))
        saved = upload_bundle_service.save_upload_bundle(inbox, [(io.BytesIO(b"<waiting video>"), "drop/copy.mp4")])
    harness.check("a folder upload of it saves nothing and says why",
                  saved["added"] == [] and "already in the inbox" in saved["refused"][0]["error"], str(saved))
    listed = sorted(path.name for path in inbox.iterdir() if not path.name.startswith("."))
    harness.check("the inbox still holds only the first", listed == ["Jonas-weekly.mp4"], str(listed))


def test_a_drop_of_new_videos_with_two_copies_saves_one_copy_and_every_other_video():
    root = Path(tempfile.mkdtemp())
    inbox, output = root / "inbox", root / "output"
    inbox.mkdir()
    output.mkdir()
    with _Swap(root, inbox, output):
        saved = upload_bundle_service.save_upload_bundle(inbox, [(io.BytesIO(b"<video a>"), "drop/AcmeSync.mp4"),
                                                                 (io.BytesIO(b"<video a>"), "drop/AcmeSync-copy.mp4"),
                                                                 (io.BytesIO(b"<video b>"), "drop/Jonas-weekly.mp4")])
    harness.check("the two different videos are saved, the second copy skipped",
                  saved["added"] == ["AcmeSync.mp4", "Jonas-weekly.mp4"]
                  and saved["refused"][0]["kind"] == "in_upload" and saved["refused"][0]["same_as"] == "AcmeSync.mp4",
                  str(saved))


def test_a_drop_of_parts_saves_each_as_its_own_recording_and_a_transcript_goes_with_its_namesake():
    # An upload of several files no longer waits for "one meeting or separate?"; each
    # recording is its own, merged later in the inbox when it is one meeting
    root = Path(tempfile.mkdtemp())
    inbox, output = root / "inbox", root / "output"
    inbox.mkdir()
    output.mkdir()
    with _Swap(root, inbox, output):
        saved = upload_bundle_service.save_upload_bundle(inbox, [(io.BytesIO(b"<a>"), "drop/Weekly part 1.mp4"),
                                                                 (io.BytesIO(b"<b>"), "drop/Weekly part 2.mp4"),
                                                                 (io.BytesIO(b"WEBVTT"), "drop/weekly part 2.vtt"),
                                                                 (io.BytesIO(b"WEBVTT"), "drop/notes.vtt")])
    listed = sorted(path.name for path in inbox.iterdir() if not path.name.startswith("."))
    harness.check("each part its own recording, the transcript in a folder with its recording",
                  listed == ["Weekly part 1.mp4", "Weekly part 2"]
                  and sorted(path.name for path in (inbox / "Weekly part 2").iterdir())
                  == ["Weekly part 2.mp4", "weekly part 2.vtt"], str(listed))
    harness.check("a transcript no recording of its name came with is named", len(saved["refused"]) == 1
                  and saved["refused"][0]["name"] == "notes.vtt", str(saved))
    harness.check("no holding folder is left", not any((inbox / upload_bundle_service.STAGING_DIR_NAME).iterdir()))


def test_a_failed_upload_leaves_no_holding_folder():
    root = Path(tempfile.mkdtemp())
    inbox, output = root / "inbox", root / "output"
    inbox.mkdir()
    output.mkdir()
    original = recording_hash_service.compute_recording_hash

    def fail(path):
        raise OSError("disk full")

    recording_hash_service.compute_recording_hash = fail
    try:
        with _Swap(root, inbox, output):
            try:
                upload_bundle_service.save_upload_bundle(inbox, [(io.BytesIO(b"<video a>"), "drop/Weekly.mp4")])
                raised = ""
            except OSError as error:
                raised = str(error)
    finally:
        recording_hash_service.compute_recording_hash = original
    harness.check("the planted disk-full error is the one that ended the upload", raised == "disk full", raised)
    staging = inbox / upload_bundle_service.STAGING_DIR_NAME
    left = sorted(path.name for path in staging.iterdir()) if staging.exists() else []
    harness.check("the holding folder is removed", left == [], str(left))


def test_a_refused_upload_is_named_as_it_was_uploaded():
    root = Path(tempfile.mkdtemp())
    inbox, output = root / "inbox", root / "output"
    inbox.mkdir()
    output.mkdir()
    (inbox / "Jonas-weekly.mp4").write_bytes(b"<waiting video>")
    with _Swap(root, inbox, output):
        try:
            upload_service.save_upload(io.BytesIO(b"<waiting video>"), inbox, "Jonas-weekly.mp4")
            refusal = "saved"
        except DuplicateRecordingError as exc:
            refusal = str(exc)
    harness.check("the refusal names the file sent, never the free name it would have had",
                  refusal.startswith("Jonas-weekly.mp4 is already in the inbox"), refusal)


def test_an_upload_never_writes_over_the_part_file_another_upload_claimed():
    """Two uploads of one name at once both found the name free, and both wrote one `<name>.part`."""

    root = Path(tempfile.mkdtemp())
    inbox, output = root / "inbox", root / "output"
    inbox.mkdir()
    output.mkdir()
    original = upload_service.choose_upload_path
    claimed = inbox / "Weekly.mp4.part"

    def choose_as_the_other_upload_claims(inbox_root, filename):
        target = original(inbox_root, filename)
        if not claimed.exists():
            claimed.write_bytes(b"<the other upload>")
        return target

    upload_service.choose_upload_path = choose_as_the_other_upload_claims
    try:
        with _Swap(root, inbox, output):
            saved = upload_service.save_upload(io.BytesIO(b"<this upload>"), inbox, "Weekly.mp4")
    except Exception as exc:
        saved = f"{type(exc).__name__}: {exc}"
    finally:
        upload_service.choose_upload_path = original
    harness.check("the other upload's part file is untouched, and this one lands under a free name",
                  claimed.read_bytes() == b"<the other upload>" and isinstance(saved, Path) and saved.name != "Weekly.mp4"
                  and saved.read_bytes() == b"<this upload>", str(saved))


def test_a_merged_recording_is_named_by_what_its_parts_share_keeping_a_last_letter_p():
    suggest = inbox_merge_service.suggest_merged_name
    harness.check("Workshop", suggest(["Workshop 1.mp4", "Workshop 2.mp4"]) == "Workshop")
    harness.check("Weekly", suggest(["Weekly part 1.mp4", "Weekly part 2.mp4"]) == "Weekly")
    harness.check("sprint review", suggest(["sprint review p1.mp4", "sprint review p2.mp4"]) == "sprint review")
    harness.check("names that share nothing take the first one's", suggest(["Kickoff.mp4", "Demo.mp4"]) == "Kickoff")


def test_ticked_inbox_recordings_merge_into_one_recording_in_parts_in_name_order():
    root = Path(tempfile.mkdtemp())
    inbox, output = root / "inbox", root / "output"
    inbox.mkdir()
    output.mkdir()
    (inbox / "Sprint 1.5").mkdir()
    for name in ("Weekly part 10.mp4", "Weekly part 2.mp4", "Jonas.m4a", "guide.pdf"):
        (inbox / name).write_bytes(name.encode())
    with _Swap(root, inbox, output):
        folder = inbox_merge_service.merge_recordings(inbox, [inbox / "Weekly part 10.mp4", inbox / "Weekly part 2.mp4"])
        refusals = []
        for videos, name in (([inbox / "Jonas.m4a"], ""), ([inbox / "Jonas.m4a", inbox / "guide.pdf"], ""),
                             ([inbox / "Jonas.m4a", folder], ""), ([inbox / "Jonas.m4a", inbox / "Jonas.m4a"], "")):
            try:
                inbox_merge_service.merge_recordings(inbox, videos, name)
                refusals.append("merged")
            except UploadRefusedError as error:
                refusals.append(str(error))
        (inbox / "a.mp4").write_bytes(b"a")
        (inbox / "b.mp4").write_bytes(b"b")
        dotted = inbox_merge_service.merge_recordings(inbox, [inbox / "a.mp4", inbox / "b.mp4"], "Sprint 1.5")
    harness.check("one folder named for what they share, parts numbered in name order",
                  folder.name == "Weekly" and sorted(path.name for path in folder.iterdir())
                  == ["01 Weekly part 2.mp4", "02 Weekly part 10.mp4"], str(list(folder.iterdir())))
    harness.check("the inbox lists it as one recording in parts", recording_parts_service.is_multi_part(folder))
    harness.check("one recording, a PDF, a recording in parts and one named twice are each refused with why",
                  [refusal.split(":")[0].split(" ")[-1] for refusal in refusals] == ["recordings", "PDF", "already", "twice"],
                  str(refusals))
    harness.check("a taken name with a dot is numbered at its end", dotted.name == "Sprint 1.5-2", dotted.name)


def test_a_branch_of_a_done_run_runs_the_line_its_branch_printed_defect():
    # Defect: a branch of a done run was refused as a recording processed already, naming the run it was branched from,
    # on the very run line the branch printed. Fix: the run folders a branch comes from are left out of the search.
    root = Path(tempfile.mkdtemp())
    inbox, output, _ = _build_done_run(root, b"<video bytes>")
    branch = output / "AcmeSync-review-from-2"
    branch.mkdir()
    (branch / "branch.json").write_text(json.dumps({"from": "AcmeSync-review", "phase": 2}), encoding="utf-8")
    twice = output / "AcmeSync-review-from-2-from-3"
    twice.mkdir()
    (twice / "branch.json").write_text(json.dumps({"from": "AcmeSync-review-from-2", "phase": 3}), encoding="utf-8")
    other = output / "AcmeSync-copy"
    other.mkdir()
    with _Swap(root, inbox, output):
        from_branch = run_command.find_duplicate_refusal(inbox / "AcmeSync-review.mp4", branch, is_forced=False)
        from_its_branch = run_command.find_duplicate_refusal(inbox / "AcmeSync-review.mp4", twice, is_forced=False)
        from_other = run_command.find_duplicate_refusal(inbox / "AcmeSync-review.mp4", other, is_forced=False)
    harness.check("a branch and a branch of it run", from_branch is None and from_its_branch is None,
                  f"{from_branch} | {from_its_branch}")
    harness.check("a folder that is no branch of it is still refused", from_other is not None, str(from_other))
