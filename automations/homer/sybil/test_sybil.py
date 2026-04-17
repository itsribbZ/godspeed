#!/usr/bin/env python3
"""
Homer L4 — SYBIL smoke tests
============================
Tests sybil.py without making real API calls. Uses dry-run and precondition
check modes exclusively. Matches test_vault.py discipline.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).parent))
import sybil as sybil_mod  # noqa: E402
from sybil import (  # noqa: E402
    SybilState,
    check_preconditions,
    escalate,
    load_state,
    save_state,
    _build_advisor_prompt,
)

PASS = "[PASS]"
FAIL = "[FAIL]"


def test_precondition_check_shape() -> bool:
    check = check_preconditions(session_id="test_shape", task_text="hello")
    d = check.to_dict()
    required_keys = {
        "has_api_key", "brain_cli_reachable", "brain_advise_command_valid",
        "not_creative_content", "session_cap_ok", "overall_pass", "failure_reasons",
    }
    return required_keys.issubset(d.keys())


def test_creative_content_detection_dialogue() -> bool:
    check = check_preconditions(
        session_id="test_creative_1",
        task_text="please write dialogue for the boss fight in chapter 3",
    )
    return not check.not_creative_content


def test_creative_content_detection_lore() -> bool:
    check = check_preconditions(
        session_id="test_creative_2",
        task_text="write lore for the ancient temple of forgotten kings",
    )
    return not check.not_creative_content


def test_non_creative_content_passes() -> bool:
    check = check_preconditions(
        session_id="test_noncreative",
        task_text="fix the compile error in foo.cpp line 42",
    )
    return check.not_creative_content


def test_session_state_default() -> bool:
    state = load_state(session_id="test_fresh_session_nonexistent_xyz")
    return state.escalations_used == 0 and state.escalations_cap == 2 and not state.is_capped()


def test_session_cap_detection() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="sybil_test_cap_"))
    original = sybil_mod.SYBIL_STATE_DIR
    sybil_mod.SYBIL_STATE_DIR = tmp
    try:
        state = SybilState(session_id="test_capped", escalations_used=2, escalations_cap=2)
        save_state(state)
        reloaded = load_state("test_capped")
        return reloaded.is_capped() and reloaded.escalations_used == 2
    finally:
        sybil_mod.SYBIL_STATE_DIR = original
        shutil.rmtree(tmp, ignore_errors=True)


def test_dry_run_escalate_returns_preconditions() -> bool:
    result = escalate(
        stuck_task="fake task for dry run — compile the foo module",
        approaches_tried=["tried extract class", "tried split file"],
        blocker="circular import with bar.py",
        session_id="test_dry_run_xyz",
        dry_run=True,
    )
    # Regardless of whether preconditions pass, the result MUST include preconditions
    return "preconditions" in result


def test_advisor_prompt_format() -> bool:
    prompt = _build_advisor_prompt(
        stuck_task="refactor the foo module",
        approaches_tried=["extract class", "split file"],
        blocker="circular import with bar.py",
    )
    return (
        "STUCK TASK:" in prompt
        and "APPROACHES TRIED:" in prompt
        and "SPECIFIC BLOCKER:" in prompt
        and "refactor the foo module" in prompt
        and "circular import" in prompt
        and "extract class" in prompt
    )


def test_advisor_prompt_empty_approaches() -> bool:
    prompt = _build_advisor_prompt(
        stuck_task="first attempt at a new problem",
        approaches_tried=[],
        blocker="no starting point identified",
    )
    return "nothing yet" in prompt and "first attempt" in prompt


def test_state_roundtrip() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="sybil_rt_"))
    original = sybil_mod.SYBIL_STATE_DIR
    sybil_mod.SYBIL_STATE_DIR = tmp
    try:
        state = SybilState(
            session_id="rt_test",
            escalations_used=1,
            last_escalation_at="2026-04-11T14:00:00",
            escalations_log=[{"at": "2026-04-11T14:00:00", "task_excerpt": "x", "blocker_excerpt": "y"}],
        )
        save_state(state)
        reloaded = load_state("rt_test")
        return (
            reloaded.escalations_used == 1
            and reloaded.last_escalation_at == "2026-04-11T14:00:00"
            and len(reloaded.escalations_log) == 1
            and reloaded.escalations_log[0]["task_excerpt"] == "x"
        )
    finally:
        sybil_mod.SYBIL_STATE_DIR = original
        shutil.rmtree(tmp, ignore_errors=True)


def test_failure_reasons_populated_on_creative() -> bool:
    check = check_preconditions(
        session_id="test_fail_reasons",
        task_text="please write lore for the ancient temple",
    )
    return len(check.failure_reasons) > 0 and any("Sacred Rule 6" in r for r in check.failure_reasons)


def test_session_id_safe_filename() -> bool:
    # Session IDs with special chars should still produce valid state files
    tmp = Path(tempfile.mkdtemp(prefix="sybil_safe_"))
    original = sybil_mod.SYBIL_STATE_DIR
    sybil_mod.SYBIL_STATE_DIR = tmp
    try:
        state = SybilState(session_id="weird/session:id*with!chars")
        save_state(state)
        reloaded = load_state("weird/session:id*with!chars")
        return reloaded.session_id == "weird/session:id*with!chars"
    finally:
        sybil_mod.SYBIL_STATE_DIR = original
        shutil.rmtree(tmp, ignore_errors=True)


def test_escalate_fails_on_creative_dry_run() -> bool:
    result = escalate(
        stuck_task="write dialogue for the knight meeting the dragon",
        approaches_tried=[],
        blocker="need creative direction",
        session_id="test_creative_escalate",
        dry_run=True,
    )
    # Even in dry-run, creative content MUST fail preconditions
    return result.get("ok") is False and "Sacred Rule 6" in result.get("reason", "")


TESTS = [
    ("precondition_check_shape", test_precondition_check_shape),
    ("creative_content_detection_dialogue", test_creative_content_detection_dialogue),
    ("creative_content_detection_lore", test_creative_content_detection_lore),
    ("non_creative_content_passes", test_non_creative_content_passes),
    ("session_state_default", test_session_state_default),
    ("session_cap_detection", test_session_cap_detection),
    ("dry_run_escalate_returns_preconditions", test_dry_run_escalate_returns_preconditions),
    ("advisor_prompt_format", test_advisor_prompt_format),
    ("advisor_prompt_empty_approaches", test_advisor_prompt_empty_approaches),
    ("state_roundtrip", test_state_roundtrip),
    ("failure_reasons_populated_on_creative", test_failure_reasons_populated_on_creative),
    ("session_id_safe_filename", test_session_id_safe_filename),
    ("escalate_fails_on_creative_dry_run", test_escalate_fails_on_creative_dry_run),
]


def run_all() -> int:
    passed = 0
    failed = 0
    print("Homer SYBIL smoke tests")
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
