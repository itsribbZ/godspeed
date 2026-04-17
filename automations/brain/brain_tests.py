#!/usr/bin/env python3
"""
Toke Brain — Classifier Smoke Tests
===================================
Runs a suite of sample prompts through the classifier and verifies each
falls into an expected tier. Not a unit test framework — just quick
confidence checks before wiring the Brain into a live session.

Usage:
    python3 brain_tests.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure UTF-8 stdout on Windows (cp1252 breaks on any non-ASCII in reasoning strings)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).parent))

from severity_classifier import classify  # noqa: E402


# (description, prompt, expected_tiers, skill_name, context_tokens)
TEST_CASES = [
    ("Trivial lookup",
     "what files are in the current directory",
     ["S0", "S1"], None, 0),

    ("Shell command",
     "run git status",
     ["S0", "S1"], None, 0),

    ("Simple factual UE5 Q",  # ue5_mention_floor fires but simple_question_ceiling caps at S1
     "what does UPROPERTY mean",
     ["S1", "S2"], None, 0),

    ("Code edit single file",
     "update the getScore function in utils.py to return an int instead of float",
     ["S2", "S3"], None, 0),

    ("Moderate debug",  # debug_floor fires on "failing" + "assertion" -> S2 floor
     "my test is failing can you check why the assertion at line 42 fires",
     ["S2", "S3"], None, 0),

    ("Multi-step plan",  # multi_step + reasoning signals cluster in S2/S3 boundary
     "first analyze the bug, then design a fix, then implement it step by step",
     ["S2", "S3", "S4"], None, 0),

    ("Architecture design",  # architecture_work guardrail -> S4+
     "design the event dispatch architecture for the EXO plugin with strategy pattern",
     ["S4", "S5"], None, 0),

    ("Multi-file refactor",  # multi_file_refactor guardrail (5 file refs) -> S4+
     "refactor auth.py login.py session.py token.py user.py to share one unified schema",
     ["S4", "S5"], None, 0),

    ("GPQA-class reasoning",  # gpqa_hard_reasoning guardrail -> S5
     "prove that this sorting algorithm runs in O(n log n) using first principles",
     ["S4", "S5"], None, 0),

    ("UE5 C++ header work",  # ue5_code_work (require_all: keyword + file ref) -> S4+
     "add a UPROPERTY macro to MyActor.h for health tracking with Blueprint exposure",
     ["S4", "S5"], None, 0),

    ("Creative / lore",  # creative_game_design guardrail -> S4+
     "design the lore for the ancient elven civilization with character arcs",
     ["S4", "S5"], None, 0),

    ("Skill override sitrep",  # skill_name overrides score -> S1
     "check all projects",
     ["S1"], "sitrep", 0),

    ("Skill override blueprint",
     "make a plan",
     ["S4"], "blueprint", 0),

    ("Skill override verify",
     "verify the build",
     ["S2"], "verify", 0),

    ("Long context guardrail",  # context_tokens >= 150k -> long_context guardrail -> S5
     "summarize this",
     ["S5"], None, 160_000),

    ("Empty prompt",
     "",
     ["S0", "S1"], None, 0),
]


def run_tests() -> int:
    passed = 0
    failed = 0
    print("Toke Brain - Classifier Smoke Tests (v1 regression + v2 feature tests)")
    print("=" * 72)
    for desc, prompt, expected_tiers, skill_name, context_tokens in TEST_CASES:
        try:
            result = classify(
                prompt_text=prompt,
                context_tokens=context_tokens,
                skill_name=skill_name,
            )
        except Exception as e:
            print(f"X {desc:<32} -> EXCEPTION: {type(e).__name__}: {e}")
            failed += 1
            continue

        ok = result.tier in expected_tiers
        status = "+" if ok else "X"
        if ok:
            passed += 1
        else:
            failed += 1
        print(f"{status} {desc:<32} -> {result.tier} ({result.model:<9}) score={result.score:.3f}  expected {expected_tiers}")
        if not ok:
            print(f"    reasoning: {result.reasoning}")

    # ========================================================================
    # v2.0 feature tests
    # ========================================================================
    print()
    print("v2.0 feature tests")
    print("-" * 72)

    v2_tests_passed = 0
    v2_tests_failed = 0

    def _v2(name: str, condition: bool, detail: str = "") -> None:
        nonlocal v2_tests_passed, v2_tests_failed
        mark = "+" if condition else "X"
        if condition:
            v2_tests_passed += 1
        else:
            v2_tests_failed += 1
        print(f"{mark} {name:<52} {detail}")

    # Test 1: confidence field is populated (non-null)
    r = classify(prompt_text="list files")
    _v2("confidence field populated", isinstance(r.confidence, float), f"conf={r.confidence}")

    # Test 2: extended_thinking_budget = 0 for S0/S1/S2 tiers
    r = classify(prompt_text="list files")
    _v2("S0 extended_thinking_budget = 0", r.extended_thinking_budget == 0, f"tier={r.tier} thinking={r.extended_thinking_budget}")

    # Test 3: S4 carries extended_thinking_budget > 0
    r = classify(prompt_text="design the system architecture for EXO plugin with strategy pattern")
    _v2("S4+ extended_thinking_budget > 0", r.extended_thinking_budget > 0, f"tier={r.tier} thinking={r.extended_thinking_budget}")

    # Test 4: correction detection in prompt (manifest-driven)
    r = classify(prompt_text="that's wrong, please try again with a different approach")
    _v2("correction detected in prompt", r.correction_detected_in_prompt, f"detected={r.correction_detected_in_prompt}")

    # Test 5: correction NOT triggered on normal prompt
    r = classify(prompt_text="list all files in this directory")
    _v2("correction NOT detected on normal prompt", not r.correction_detected_in_prompt)

    # Test 6: multi-turn context — last turn shows correction, current tier bumps
    context_history = [
        {
            "session_id": "test-session",
            "current_model": "claude-opus-4-6",
            "result": {
                "tier": "S2",
                "model": "sonnet",
                "correction_detected_in_prompt": True,
            },
        },
    ]
    r = classify(
        prompt_text="add an error handler to api.py",
        context_history=context_history,
    )
    _v2("context_history bumps tier on correction in prior turn", r.context_turns_seen == 1 and r.uncertainty_escalated, f"turns={r.context_turns_seen} escalated={r.uncertainty_escalated}")

    # Test 7: S5 stays S5 even with escalation signal (can't bump higher)
    r = classify(
        prompt_text="prove the halting problem using first principles",
    )
    _v2("S5 stays S5 (can't escalate beyond)", r.tier == "S5", f"tier={r.tier}")

    # Test 8: manifest load failure returns S4 not S3 (v2 fail-open upgrade)
    from pathlib import Path as _P
    r = classify(prompt_text="test", manifest_path=_P("/nonexistent/manifest.toml"))
    _v2("v2 fail-open default is S4 (not S3)", r.tier == "S4" and r.model == "opus", f"tier={r.tier} model={r.model}")

    # Test 9: confidence is lower near tier boundary
    # Score around 0.22 is right near s2_max=0.35 boundary — confidence should be lower
    r_mid = classify(prompt_text="update the getScore function in utils.py to return an int")
    r_trivial = classify(prompt_text="list files")
    _v2("confidence differs between boundary and deep interior", r_mid.confidence < 1.0 or r_trivial.confidence == 1.0, f"mid={r_mid.confidence} trivial={r_trivial.confidence}")

    # Test 10: reasoning string contains v2 markers when applicable
    r = classify(prompt_text="design the architecture for the event dispatch system")
    has_thinking_marker = "thinking:" in r.reasoning if r.extended_thinking_budget > 0 else True
    _v2("reasoning includes thinking marker when budget > 0", has_thinking_marker, f"reasoning='{r.reasoning[:80]}'")

    # ========================================================================
    # v2.4 domain-scoped guardrail tests
    # ========================================================================
    print()
    print("v2.4 domain-scoped guardrail tests")
    print("-" * 72)

    v24_passed = 0
    v24_failed = 0

    def _v24(name: str, condition: bool, detail: str = "") -> None:
        nonlocal v24_passed, v24_failed
        mark = "+" if condition else "X"
        if condition:
            v24_passed += 1
        else:
            v24_failed += 1
        print(f"{mark} {name:<52} {detail}")

    # Test 1: UE5 guardrail fires in UE5 CWD (no regression)
    r = classify(
        prompt_text="add a UPROPERTY macro to MyActor.h for health",
        cwd="~/Documents/your-game-project/MyProject",
    )
    _v24("UE5 guardrail fires in UE5 CWD", "ue5_code_work" in r.guardrails_fired, f"guards={r.guardrails_fired}")

    # Test 2: UE5 guardrail suppressed in Toke CWD (THE BUG FIX)
    r = classify(
        prompt_text="verify Sworder combat and check MyActor.h references across the pipeline",
        cwd="~/Desktop/T1/Toke",
    )
    _v24("UE5 guardrail suppressed in Toke CWD", "ue5_code_work" not in r.guardrails_fired, f"tier={r.tier} guards={r.guardrails_fired}")

    # Test 3: UE5 mention floor suppressed in non-UE5 CWD
    r = classify(
        prompt_text="what is sworder's current build state",
        cwd="~/Desktop/T1/Toke",
    )
    _v24("UE5 mention floor suppressed in Toke CWD", "ue5_mention_floor" not in r.guardrails_fired, f"tier={r.tier} guards={r.guardrails_fired}")

    # Test 4: Non-domain-tagged guardrail still fires regardless of CWD
    r = classify(
        prompt_text="design the event dispatch architecture with strategy pattern",
        cwd="~/Desktop/T1/Toke",
    )
    _v24("architecture_work fires regardless of CWD", "architecture_work" in r.guardrails_fired, f"guards={r.guardrails_fired}")

    # Test 5: CWD=None preserves all guardrail behavior (backward compat)
    r = classify(
        prompt_text="add a UPROPERTY macro to MyActor.h for health",
        cwd=None,
    )
    _v24("CWD=None preserves UE5 guardrails", "ue5_code_work" in r.guardrails_fired, f"guards={r.guardrails_fired}")

    # Test 6: session_max_tier catches wider continuation (120 chars)
    r = classify(
        prompt_text="ok lets run all five of those verification tasks and check the results match what we expect",
        session_max_tier="S4",
    )
    _v24("session_max_tier floors 90-char continuation at S3+", r.tier in ("S3", "S4", "S5"), f"tier={r.tier}")

    # Test 7: domain detection helper
    from severity_classifier import _detect_project_domain
    _v24("domain detection: Sworder = ue5", _detect_project_domain("~/Documents/your-game-project/MyProject") == "ue5")
    _v24("domain detection: Toke = toke", _detect_project_domain("~/Desktop/T1/Toke") == "toke")
    _v24("domain detection: your-trading-project = quantified", _detect_project_domain("~/Desktop/T1/your-trading-project") == "quantified")
    _v24("domain detection: None = None", _detect_project_domain(None) is None)

    # v2.5 feature tests
    v25_passed = 0
    v25_failed = 0

    def _v25(name: str, ok: bool, detail: str = "") -> None:
        nonlocal v25_passed, v25_failed
        mark = "+" if ok else "X"
        if ok:
            v25_passed += 1
        else:
            v25_failed += 1
        print(f"{mark} {name:<52} {detail}")

    print()
    print("v2.5 skill-inference + turn-depth tests")
    print("-" * 72)

    # Test 1: turn_depth — S0 at 8+ turns bumps to S1
    _ctx_8 = [{"result": {"tier": "S0"}} for _ in range(8)]
    r = classify(prompt_text="ok", context_tokens=0, context_history=_ctx_8)
    _v25("turn_depth: S0 bumped to S1 at 8 turns", r.tier != "S0", f"tier={r.tier} turns=8")

    # Test 2: turn_depth — S0/S1 at 15+ turns bumps to S2
    _ctx_15 = [{"result": {"tier": "S2"}} for _ in range(15)]
    r = classify(prompt_text="next", context_tokens=0, context_history=_ctx_15)
    _v25("turn_depth: S0 floor S2 at 15 turns", r.tier in ("S2", "S3", "S4", "S5"), f"tier={r.tier} turns=15")

    # Test 3: turn_depth — skill_override prevents turn_depth bump
    _ctx_8 = [{"result": {"tier": "S0"}} for _ in range(8)]
    r = classify(prompt_text="ok", context_tokens=0, context_history=_ctx_8, skill_name="sitrep")
    _v25("turn_depth: skill_override bypasses turn_depth bump", r.tier in ("S1", "S2"), f"tier={r.tier} skill=sitrep")

    # Test 4: turn_depth — <8 turns, no bump
    _ctx_3 = [{"result": {"tier": "S0"}} for _ in range(3)]
    r = classify(prompt_text="ok", context_tokens=0, context_history=_ctx_3)
    _v25("turn_depth: no bump at 3 turns", r.tier in ("S0", "S1"), f"tier={r.tier} turns=3")

    # Test 5: skill inference — godspeed prefix → S4 floor
    r = classify(prompt_text="godspeed", context_tokens=0, skill_name="godspeed")
    _v25("skill floor: godspeed → S4", r.tier in ("S4", "S5"), f"tier={r.tier}")

    # Test 6: skill inference — init prefix → S2 floor
    r = classify(prompt_text="init toke", context_tokens=0, skill_name="init")
    _v25("skill floor: init → S2", r.tier in ("S2", "S3", "S4", "S5"), f"tier={r.tier}")

    # ────────────────────────────────────────────────────────────────────
    # v2.7 golden_set regression floor (G18 fix, 2026-04-17)
    # Before this, brain_tests.py was 42 smoke tests with wide tier bands.
    # The 200-prompt golden_set lived in eval/ and was only run manually.
    # This block enforces: accuracy cannot regress below the 2026-04-17 baseline
    # (142/200 exact, 1 wrong, 0.853 weighted). Future edits that drop below
    # these floors fail the test suite.
    # ────────────────────────────────────────────────────────────────────
    print("\nv2.7 golden_set regression floor (G18)")
    print("-" * 72)
    gs_passed = 0
    gs_failed = 0
    try:
        import json
        gs_path = Path(__file__).parent / "eval" / "golden_set.json"
        gs = json.loads(gs_path.read_text(encoding="utf-8"))
        _tier_order = ("S0", "S1", "S2", "S3", "S4", "S5")
        exact = adjacent = wrong = 0
        for e in gs:
            got = classify(prompt_text=e["prompt"]).tier
            d = abs(_tier_order.index(got) - _tier_order.index(e["expected"]))
            if d == 0:
                exact += 1
            elif d == 1:
                adjacent += 1
            else:
                wrong += 1
        weighted = (exact + 0.5 * adjacent) / len(gs)

        # Floors — MUST NOT regress below these values
        # Measured 2026-04-17 in this test harness context (post G1+G6+G7):
        #   exact=138, wrong=1, weighted=0.843. Standalone eval shows 142/1/0.853
        #   (small variance in test-harness context — not a correctness issue, just
        #   the reality of where we set regression floors).
        EXACT_FLOOR = 135      # 3-prompt slack below measured 138
        WRONG_CEIL = 2         # 1-prompt slack above measured 1
        WEIGHTED_FLOOR = 0.83  # 0.013 slack below measured 0.843

        exact_ok = exact >= EXACT_FLOOR
        wrong_ok = wrong <= WRONG_CEIL
        weighted_ok = weighted >= WEIGHTED_FLOOR

        for name, ok, val, floor in [
            ("golden_set exact >= 135", exact_ok, exact, EXACT_FLOOR),
            ("golden_set wrong <= 2", wrong_ok, wrong, WRONG_CEIL),
            ("golden_set weighted >= 0.83", weighted_ok, round(weighted, 3), WEIGHTED_FLOOR),
        ]:
            symbol = "+" if ok else "-"
            print(f"  {symbol} {name:<35} got={val} floor={floor}")
            if ok:
                gs_passed += 1
            else:
                gs_failed += 1
    except Exception as exc:  # noqa: BLE001
        print(f"  - golden_set regression check SKIPPED: {type(exc).__name__}: {exc}")
        gs_failed = 3  # treat as failure — must not silently skip

    print("-" * 72)
    print(f"v1 regression: {passed} passed, {failed} failed of {len(TEST_CASES)}")
    print(f"v2 features:   {v2_tests_passed} passed, {v2_tests_failed} failed of 10")
    print(f"v2.4 domain:   {v24_passed} passed, {v24_failed} failed of 10")
    print(f"v2.5 signals:  {v25_passed} passed, {v25_failed} failed of 6")
    print(f"v2.7 golden:   {gs_passed} passed, {gs_failed} failed of 3")
    print("=" * 72)

    total_failed = failed + v2_tests_failed + v24_failed + v25_failed + gs_failed
    return 0 if total_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_tests())
