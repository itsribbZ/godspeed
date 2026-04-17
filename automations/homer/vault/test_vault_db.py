#!/usr/bin/env python3
"""
VAULT v2 — SQLite Backend Test Suite
=====================================
Smoke tests for vault_db.py. Follows the brain_tests.py pattern:
standalone runner, temp dirs, no pytest dependency.

Usage:
    python test_vault_db.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).parent))

from vault_db import VaultDB, checkpoint, durable_sleep, replay, migrate_json_to_sqlite  # noqa: E402


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_pass_count = 0
_fail_count = 0


def _check(name: str, condition: bool, detail: str = ""):
    global _pass_count, _fail_count
    if condition:
        _pass_count += 1
        print(f"  + {name}")
    else:
        _fail_count += 1
        print(f"  X {name}  ({detail})")


def _make_db() -> VaultDB:
    """Create a temp SQLite DB for testing."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return VaultDB(db_path=path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_schema_creation():
    """DB creates all 4 tables on init."""
    db = _make_db()
    report = db.health_report()
    _check("schema_creation: workflows table exists", report["workflows"] == 0)
    _check("schema_creation: schema is v2.0", report["schema"] == "v2.0")


def test_workflow_crud():
    """Create, read, update, list workflows."""
    db = _make_db()
    wf = db.create_workflow("wf_1", session_id="sess_1", topic="test_topic")
    _check("workflow_create: id matches", wf["id"] == "wf_1")
    _check("workflow_create: phase is init", wf["phase"] == "init")
    _check("workflow_create: topic set", wf["topic"] == "test_topic")

    read = db.get_workflow("wf_1")
    _check("workflow_read: returns dict", read is not None and read["id"] == "wf_1")

    db.update_workflow("wf_1", phase="plan")
    updated = db.get_workflow("wf_1")
    _check("workflow_update: phase changed", updated["phase"] == "plan")

    db.create_workflow("wf_2", session_id="sess_1", topic="second")
    all_wfs = db.list_workflows()
    _check("workflow_list: 2 workflows", len(all_wfs) == 2)

    filtered = db.list_workflows(session_id="sess_1", phase="plan")
    _check("workflow_list_filtered: 1 match", len(filtered) == 1)


def test_step_crud():
    """Create, read, complete, fail steps."""
    db = _make_db()
    db.create_workflow("wf_steps", session_id="s1", topic="steps_test")

    step = db.create_step("wf_steps", "extract")
    _check("step_create: status is pending", step["status"] == "pending")

    db.mark_step_running("wf_steps", "extract")
    running = db.get_step("wf_steps", "extract")
    _check("step_running: status is running", running["status"] == "running")

    db.complete_step("wf_steps", "extract", {"rows": 42}, attempt=0)
    done = db.get_step("wf_steps", "extract")
    _check("step_complete: status is done", done["status"] == "done")
    _check("step_complete: result stored", json.loads(done["result_json"]) == {"rows": 42})

    db.create_step("wf_steps", "transform")
    db.fail_step("wf_steps", "transform", "timeout", attempt=1)
    failed = db.get_step("wf_steps", "transform")
    _check("step_fail: status is failed", failed["status"] == "failed")
    _check("step_fail: error stored", failed["error"] == "timeout")

    all_steps = db.get_workflow_steps("wf_steps")
    _check("step_list: 2 steps", len(all_steps) == 2)

    completed = db.get_completed_steps("wf_steps")
    _check("step_completed_list: 1 done", len(completed) == 1)

    next_step = db.get_next_pending_step("wf_steps")
    _check("step_next_pending: returns transform", next_step["step_name"] == "transform")


def test_step_idempotent_create():
    """create_step with same name is idempotent (INSERT OR IGNORE)."""
    db = _make_db()
    db.create_workflow("wf_idem", session_id="s1", topic="idem")
    db.create_step("wf_idem", "step_a")
    db.complete_step("wf_idem", "step_a", "result_1", 0)

    # Re-create same step — should NOT overwrite
    db.create_step("wf_idem", "step_a")
    step = db.get_step("wf_idem", "step_a")
    _check("step_idempotent: status still done", step["status"] == "done")
    _check("step_idempotent: result preserved", json.loads(step["result_json"]) == "result_1")


def test_checkpoint_decorator():
    """@checkpoint skips on replay and retries on failure."""
    db = _make_db()
    db.create_workflow("wf_cp", session_id="s1", topic="checkpoint_test")

    call_count = 0

    @checkpoint("compute", max_retries=1, backoff_base=0.01)
    def compute(workflow_id, db_arg):
        nonlocal call_count
        call_count += 1
        return {"answer": call_count}

    # First call: runs the function
    result1 = compute("wf_cp", db)
    _check("checkpoint_first_call: runs function", result1 == {"answer": 1})
    _check("checkpoint_first_call: called once", call_count == 1)

    # Second call: returns cached (replay)
    result2 = compute("wf_cp", db)
    _check("checkpoint_replay: returns cached", result2 == {"answer": 1})
    _check("checkpoint_replay: NOT called again", call_count == 1)


def test_checkpoint_retry():
    """@checkpoint retries on failure with backoff."""
    db = _make_db()
    db.create_workflow("wf_retry", session_id="s1", topic="retry_test")

    attempt_tracker = []

    @checkpoint("flaky", max_retries=2, backoff_base=0.01)
    def flaky(workflow_id, db_arg):
        attempt_tracker.append(len(attempt_tracker))
        if len(attempt_tracker) < 3:
            raise ValueError("not yet")
        return "success"

    result = flaky("wf_retry", db)
    _check("checkpoint_retry: succeeds on 3rd attempt", result == "success")
    _check("checkpoint_retry: 3 attempts made", len(attempt_tracker) == 3)

    step = db.get_step("wf_retry", "flaky")
    _check("checkpoint_retry: step status done", step["status"] == "done")


def test_checkpoint_exhausted():
    """@checkpoint raises after max_retries exhausted."""
    db = _make_db()
    db.create_workflow("wf_exhaust", session_id="s1", topic="exhaust_test")

    @checkpoint("always_fail", max_retries=1, backoff_base=0.01)
    def always_fail(workflow_id, db_arg):
        raise RuntimeError("permanent failure")

    raised = False
    try:
        always_fail("wf_exhaust", db)
    except RuntimeError:
        raised = True

    _check("checkpoint_exhausted: raises after retries", raised)
    step = db.get_step("wf_exhaust", "always_fail")
    _check("checkpoint_exhausted: step status failed", step["status"] == "failed")


def test_signals():
    """send_signal and recv_signal work as a queue."""
    db = _make_db()
    db.create_workflow("wf_sig", session_id="s1", topic="signals_test")

    db.send_signal("wf_sig", "data_ready", {"batch_id": 1})
    db.send_signal("wf_sig", "data_ready", {"batch_id": 2})
    db.send_signal("wf_sig", "other_topic", {"x": 99})

    # Consume first signal on data_ready
    sig1 = db.recv_signal("wf_sig", "data_ready")
    _check("signal_recv: first signal", sig1 == {"batch_id": 1})

    sig2 = db.recv_signal("wf_sig", "data_ready")
    _check("signal_recv: second signal", sig2 == {"batch_id": 2})

    sig3 = db.recv_signal("wf_sig", "data_ready")
    _check("signal_recv: empty after consumed", sig3 is None)

    # Other topic unaffected
    sig_other = db.recv_signal("wf_sig", "other_topic")
    _check("signal_recv: other topic works", sig_other == {"x": 99})

    all_sigs = db.list_signals("wf_sig", include_consumed=True)
    _check("signal_list: 3 total", len(all_sigs) == 3)

    unconsumed = db.list_signals("wf_sig", include_consumed=False)
    _check("signal_list_unconsumed: 0 remaining", len(unconsumed) == 0)


def test_timers():
    """create_timer, get_expired, fire_timer, check_and_fire."""
    db = _make_db()
    db.create_workflow("wf_timer", session_id="s1", topic="timer_test")

    # Timer that already expired (1 second ago)
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.create_timer("wf_timer", past, callback="step_resume")

    # Timer in the future (1 hour)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    db.create_timer("wf_timer", future, callback="step_later")

    expired = db.get_expired_timers("wf_timer")
    _check("timer_expired: 1 expired", len(expired) == 1)
    _check("timer_expired: correct callback", expired[0]["callback"] == "step_resume")

    callbacks = db.check_and_fire_timers("wf_timer")
    _check("timer_fire: 1 callback", len(callbacks) == 1 and callbacks[0] == "step_resume")

    # After firing, no more expired
    expired2 = db.get_expired_timers("wf_timer")
    _check("timer_after_fire: 0 expired", len(expired2) == 0)


def test_durable_sleep():
    """durable_sleep creates a timer that can be checked."""
    db = _make_db()
    db.create_workflow("wf_sleep", session_id="s1", topic="sleep_test")

    timer_id = durable_sleep(db, "wf_sleep", "wake_step", seconds=0.0)
    _check("durable_sleep: returns timer_id", timer_id > 0)

    # Since we slept 0 seconds, it should be immediately expired
    time.sleep(0.05)  # tiny margin
    callbacks = db.check_and_fire_timers("wf_sleep")
    _check("durable_sleep: timer fires", "wake_step" in callbacks)


def test_replay():
    """replay() returns workflow state for resumption."""
    db = _make_db()
    db.create_workflow("wf_replay", session_id="s1", topic="replay_test")
    db.create_step("wf_replay", "step1")
    db.create_step("wf_replay", "step2")
    db.create_step("wf_replay", "step3")
    db.complete_step("wf_replay", "step1", "done_1", 0)

    state = replay(db, "wf_replay")
    _check("replay: workflow found", state["workflow"] is not None)
    _check("replay: 1 completed step", len(state["completed_steps"]) == 1)
    _check("replay: next is step2", state["next_step"]["step_name"] == "step2")
    _check("replay: resumable", state["resumable"] is True)
    _check("replay: all 3 steps", len(state["all_steps"]) == 3)


def test_replay_done_workflow():
    """replay() on a completed workflow is not resumable."""
    db = _make_db()
    db.create_workflow("wf_done", session_id="s1", topic="done_test")
    db.create_step("wf_done", "only_step")
    db.complete_step("wf_done", "only_step", "result", 0)
    db.update_workflow("wf_done", phase="done")

    state = replay(db, "wf_done")
    _check("replay_done: not resumable", state["resumable"] is False)


def test_replay_not_found():
    """replay() on nonexistent workflow."""
    db = _make_db()
    state = replay(db, "nonexistent")
    _check("replay_not_found: not resumable", state["resumable"] is False)


def test_json_migration():
    """migrate_json_to_sqlite() imports v1 JSON checkpoints."""
    db = _make_db()
    tmpdir = Path(tempfile.mkdtemp())

    # Create fake v1 checkpoints
    for i in range(3):
        cp = {
            "schema_version": "1.0",
            "checkpoint_id": f"homer_test_{i}",
            "session_id": "sess_migration",
            "skill": "homer",
            "topic": f"topic_{i}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "phase": "done",
            "tasks": [{"id": 1, "description": f"task_{i}"}],
            "agents": [],
            "escalations": [],
            "memory_refs": [],
            "tokens_in": 100 * i,
            "tokens_out": 50 * i,
            "cost_usd": 0.1 * i,
            "outcome": "pass",
            "notes": f"note_{i}",
        }
        (tmpdir / f"homer_test_{i}.json").write_text(json.dumps(cp), encoding="utf-8")

    count = migrate_json_to_sqlite(tmpdir, db)
    _check("migration: 3 imported", count == 3)

    # Verify data
    wf = db.get_workflow("homer_test_1")
    _check("migration: workflow exists", wf is not None)
    _check("migration: topic preserved", wf["topic"] == "topic_1")
    _check("migration: phase preserved", wf["phase"] == "done")

    meta = json.loads(wf["metadata_json"])
    _check("migration: metadata has tasks", len(meta.get("tasks", [])) == 1)
    _check("migration: metadata has source", meta.get("source") == "json_migration")

    # Idempotent — re-run should skip
    count2 = migrate_json_to_sqlite(tmpdir, db)
    _check("migration_idempotent: 0 re-imported", count2 == 0)


def test_health_report():
    """health_report returns correct counts."""
    db = _make_db()
    db.create_workflow("wf_h1", session_id="s1", topic="t1", phase="init")
    db.create_workflow("wf_h2", session_id="s1", topic="t2", phase="done")
    db.create_step("wf_h1", "s1")
    db.complete_step("wf_h1", "s1", "ok", 0)
    db.create_step("wf_h1", "s2")
    db.fail_step("wf_h1", "s2", "err", 0)
    db.send_signal("wf_h1", "sig1", {})
    db.create_timer("wf_h1", datetime.now(timezone.utc), "cb")

    report = db.health_report()
    _check("health: 2 workflows", report["workflows"] == 2)
    _check("health: 2 steps", report["steps"] == 2)
    _check("health: 1 done step", report["steps_done"] == 1)
    _check("health: 1 failed step", report["steps_failed"] == 1)
    _check("health: 1 signal", report["signals"] == 1)
    _check("health: 1 timer", report["timers"] == 1)
    _check("health: phase_counts correct", report["phase_counts"] == {"init": 1, "done": 1})


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_all() -> tuple[int, int]:
    print("VAULT v2 — SQLite Backend Tests")
    print("=" * 60)

    test_schema_creation()
    test_workflow_crud()
    test_step_crud()
    test_step_idempotent_create()
    test_checkpoint_decorator()
    test_checkpoint_retry()
    test_checkpoint_exhausted()
    test_signals()
    test_timers()
    test_durable_sleep()
    test_replay()
    test_replay_done_workflow()
    test_replay_not_found()
    test_json_migration()
    test_health_report()

    print("=" * 60)
    print(f"  {_pass_count} passed, {_fail_count} failed of {_pass_count + _fail_count}")
    return _pass_count, _fail_count


if __name__ == "__main__":
    p, f = run_all()
    sys.exit(1 if f > 0 else 0)
