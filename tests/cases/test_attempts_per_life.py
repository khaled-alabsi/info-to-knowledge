"""A blocked step gets its attempt budget again in the next life of the run, as the README's "fix the cause, then
`--resume`" promises. With one attempt a step, the run store counted every life's attempts together, so a frame that
blocked could never be asked again: its resume blocked at once with no model call (2026-09-30, frame 319).
"""

import tempfile
from pathlib import Path

from support import harness

from models.run_identity import RunIdentity
from services.run_store_service import RunStore


def test_a_new_life_counts_only_its_own_attempts():
    folder = Path(tempfile.mkdtemp())
    store = RunStore.create(folder / "run.json", RunIdentity(run_id="t", repository=str(folder), module="m.mp4",
                                                             output_root=str(folder)), budgets={}, models=[])
    store.record_attempt("frames_processing", "frame-001", {"n": 1, "accepted": False,
                                              "validation": {"findings": [{"code": "X", "detail": "d", "fix": "f"}]}})
    harness.check("the life that made the attempt counts it", store.count_attempts_this_life("frames_processing", "frame-001") == 1)
    resumed = RunStore.load(folder / "run.json")
    harness.check("a resumed life starts with no attempt of its own", resumed.count_attempts_this_life("frames_processing", "frame-001") == 0)
    harness.check("the run's total still holds every attempt", resumed.count_attempts("frames_processing", "frame-001") == 1)
    resumed.record_attempt("frames_processing", "frame-001", {"n": 2, "accepted": True})
    harness.check("and counts its own from there", resumed.count_attempts_this_life("frames_processing", "frame-001") == 1
                  and resumed.count_attempts("frames_processing", "frame-001") == 2)


def test_an_unknown_item_status_is_refused_with_a_value_error_and_a_read_writes_nothing():
    folder = Path(tempfile.mkdtemp())
    identity = RunIdentity(run_id="t", repository=str(folder), module="m.mp4", output_root=str(folder))
    store = RunStore.create(folder / "run.json", identity, budgets={}, models=[])
    try:
        store.set_item_status("frame-001", "not-a-status")
        outcome = "accepted"
    except ValueError:
        outcome = "refused"
    except AssertionError:
        outcome = "assertion"
    harness.check("a status no item can have is refused as a value error, kept under python -O too", outcome == "refused", outcome)
    bare = RunStore.create(folder / "bare.json", identity, budgets={}, models=[])
    bare.data.pop("items", None)
    bare.data.pop("attempts", None)
    bare.list_items()
    bare.count_attempts("frames_processing", "frame-001")
    harness.check("reading the items and the attempts adds neither section to the record",
                  "items" not in bare.data and "attempts" not in bare.data, str(sorted(bare.data)))
    bare.set_item_status("frame-001", "pending")
    harness.check("writing an item creates its section", list(bare.list_items()) == ["frame-001"])
