#!/usr/bin/env python3
"""
Homer L0 — VAULT smoke tests
============================
Stdlib-only tests following brain_tests.py pattern. Run via homer_cli.py test
or directly: python vault/test_vault.py

All tests use a temp state dir — no pollution of real vault/state/.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

# Windows UTF-8 hardening
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).parent))
from vault import VaultStore, Checkpoint, SCHEMA_VERSION  # noqa: E402

PASS = "[PASS]"
FAIL = "[FAIL]"


def _fresh_store() -> tuple[VaultStore, Path]:
    tmp = Path(tempfile.mkdtemp(prefix="homer_vault_test_"))
    return VaultStore(state_dir=tmp), tmp


def test_create_and_read() -> bool:
    store, tmp = _fresh_store()
    try:
        cp = store.create(topic="unit_test", session_id="test_session_1")
        assert cp.checkpoint_id.startswith("homer_unit_test_")
        roundtrip = store.read(cp.checkpoint_id)
        assert roundtrip is not None
        assert roundtrip.checkpoint_id == cp.checkpoint_id
        assert roundtrip.session_id == "test_session_1"
        assert roundtrip.topic == "unit_test"
        assert roundtrip.schema_version == SCHEMA_VERSION
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_schema_version_pinned() -> bool:
    store, tmp = _fresh_store()
    try:
        cp = store.create(topic="schema", session_id="s")
        return cp.schema_version == SCHEMA_VERSION
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_invalid_phase_rejected() -> bool:
    cp = Checkpoint(
        checkpoint_id="homer_bad_20260411_000000_0000",
        session_id="s",
        topic="t",
        created_at="2026-04-11T00:00:00",
        phase="WRONG_PHASE",
    )
    ok, err = cp.validate()
    return (not ok) and "phase" in err


def test_empty_topic_rejected() -> bool:
    cp = Checkpoint(
        checkpoint_id="homer_x_20260411_000000_0000",
        session_id="s",
        topic="",
        created_at="2026-04-11T00:00:00",
        phase="init",
    )
    ok, err = cp.validate()
    return (not ok) and "topic" in err


def test_list_ordering_newest_first() -> bool:
    store, tmp = _fresh_store()
    try:
        store.create(topic="first", session_id="s1")
        time.sleep(0.02)
        store.create(topic="second", session_id="s2")
        time.sleep(0.02)
        store.create(topic="third", session_id="s3")
        lst = store.list_all()
        return (
            len(lst) == 3
            and lst[0].topic == "third"
            and lst[1].topic == "second"
            and lst[2].topic == "first"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_latest() -> bool:
    store, tmp = _fresh_store()
    try:
        assert store.latest() is None
        store.create(topic="alpha", session_id="s1")
        time.sleep(0.02)
        cp_b = store.create(topic="beta", session_id="s2")
        latest = store.latest()
        return latest is not None and latest.checkpoint_id == cp_b.checkpoint_id
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_collision_suffix_uniqueness() -> bool:
    store, tmp = _fresh_store()
    try:
        ids: set[str] = set()
        for _ in range(30):
            cp = store.create(topic="collision", session_id="s")
            ids.add(cp.checkpoint_id)
        return len(ids) == 30
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_update_phase_transition() -> bool:
    store, tmp = _fresh_store()
    try:
        cp = store.create(topic="phase_flow", session_id="s", phase="init")
        assert cp.phase == "init"
        cp.phase = "plan"
        store.update(cp)
        reread = store.read(cp.checkpoint_id)
        return reread is not None and reread.phase == "plan"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_archive_stale_noop_on_fresh() -> bool:
    store, tmp = _fresh_store()
    try:
        store.create(topic="fresh", session_id="s")
        n = store.archive_stale(threshold_seconds=3600)
        return n == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_corrupt_file_skipped_in_list() -> bool:
    store, tmp = _fresh_store()
    try:
        store.create(topic="good", session_id="s")
        corrupt = tmp / "homer_corrupt_20260411_000000_dead.json"
        corrupt.write_text("{not valid json", encoding="utf-8")
        lst = store.list_all()
        return len(lst) == 1  # corrupt skipped, good kept
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_forward_compat_unknown_keys_dropped() -> bool:
    store, tmp = _fresh_store()
    try:
        cp = store.create(topic="fwd_compat", session_id="s")
        # Inject an unknown field and re-read
        import json as _json
        path = tmp / f"{cp.checkpoint_id}.json"
        data = _json.loads(path.read_text(encoding="utf-8"))
        data["future_field_v2"] = {"anything": 42}
        path.write_text(_json.dumps(data), encoding="utf-8")
        reread = store.read(cp.checkpoint_id)
        return reread is not None and reread.topic == "fwd_compat"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_health_report_shape() -> bool:
    store, tmp = _fresh_store()
    try:
        store.create(topic="h1", session_id="s", phase="init")
        store.create(topic="h2", session_id="s", phase="plan")
        h = store.health_report()
        return (
            h["live_count"] == 2
            and h["archived_count"] == 0
            and h["phase_counts"].get("init") == 1
            and h["phase_counts"].get("plan") == 1
            and h["schema_version"] == SCHEMA_VERSION
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


TESTS = [
    ("create_and_read", test_create_and_read),
    ("schema_version_pinned", test_schema_version_pinned),
    ("invalid_phase_rejected", test_invalid_phase_rejected),
    ("empty_topic_rejected", test_empty_topic_rejected),
    ("list_ordering_newest_first", test_list_ordering_newest_first),
    ("latest", test_latest),
    ("collision_suffix_uniqueness", test_collision_suffix_uniqueness),
    ("update_phase_transition", test_update_phase_transition),
    ("archive_stale_noop_on_fresh", test_archive_stale_noop_on_fresh),
    ("corrupt_file_skipped_in_list", test_corrupt_file_skipped_in_list),
    ("forward_compat_unknown_keys_dropped", test_forward_compat_unknown_keys_dropped),
    ("health_report_shape", test_health_report_shape),
]


def run_all() -> int:
    passed = 0
    failed = 0
    print("Homer VAULT smoke tests")
    print("=" * 48)
    for name, fn in TESTS:
        try:
            ok = fn()
            if ok:
                print(f"  {PASS} {name}")
                passed += 1
            else:
                print(f"  {FAIL} {name}")
                failed += 1
        except Exception as e:
            print(f"  {FAIL} {name}  --  {type(e).__name__}: {e}")
            failed += 1
    print("=" * 48)
    print(f"{passed}/{len(TESTS)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_all())
