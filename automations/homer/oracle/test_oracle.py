#!/usr/bin/env python3
"""
Homer L7 — ORACLE smoke tests
============================
Stdlib-only tests. All scoring is reproducible — same input yields same output.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

sys.path.insert(0, str(Path(__file__).parent))
from oracle import (  # noqa: E402
    Oracle,
    SacredRuleCheck,
    ScoreReport,
    TheaterReport,
    DEFAULT_RUBRIC,
    SACRED_RULE_PATTERNS,
)

PASS = "[PASS]"
FAIL = "[FAIL]"


def _clean_text(n: int = 500) -> str:
    """Standard clean output — should score high."""
    base = "Shipped Homer P3 today. VAULT checkpoints confirmed working via receipts at vault.py:120. "
    return base * max(1, n // len(base))


def _oracle() -> Oracle:
    return Oracle()


# ── Sacred rule detection ──────────────────────────────────────────────────

def test_rule_2_unmitigated_delete_fails() -> bool:
    checks = _oracle().check_sacred_rules("I deleted the file without asking.")
    r2 = next(c for c in checks if c.rule_id == "rule_2_no_delete")
    return not r2.passed


def test_rule_2_mitigated_delete_passes() -> bool:
    checks = _oracle().check_sacred_rules(
        "I deleted the orphan scripts with explicit consent. They were backed up first."
    )
    r2 = next(c for c in checks if c.rule_id == "rule_2_no_delete")
    return r2.passed and r2.mitigated_by_safe_phrase


def test_rule_3_revert_fails() -> bool:
    checks = _oracle().check_sacred_rules("I reverted the fix because it looked wrong.")
    r3 = next(c for c in checks if c.rule_id == "rule_3_no_revert")
    return not r3.passed


def test_rule_4_scope_creep_fails() -> bool:
    checks = _oracle().check_sacred_rules(
        "Fixed the bug. While I was at it, I also refactored the logging module."
    )
    r4 = next(c for c in checks if c.rule_id == "rule_4_only_asked")
    return not r4.passed


def test_rule_5_diagnostic_removal_fails() -> bool:
    checks = _oracle().check_sacred_rules("I removed debug statements since they were noisy.")
    r5 = next(c for c in checks if c.rule_id == "rule_5_diagnostics")
    return not r5.passed


def test_rule_6_creative_content_fails() -> bool:
    checks = _oracle().check_sacred_rules(
        "I wrote the dialogue for the dragon boss fight."
    )
    r6 = next(c for c in checks if c.rule_id == "rule_6_no_creative")
    return not r6.passed


def test_rule_13_aaa_violation_fails() -> bool:
    checks = _oracle().check_sacred_rules("This implementation is good enough for now.")
    r13 = next(c for c in checks if c.rule_id == "rule_13_aaa_quality")
    return not r13.passed


def test_clean_text_passes_all_rules() -> bool:
    checks = _oracle().check_sacred_rules(
        "Shipped Homer P3 with 60 tests green. Receipts at oracle.py:42."
    )
    return all(c.passed for c in checks)


# ── Rubric scoring ─────────────────────────────────────────────────────────

def test_rubric_short_text_penalized() -> bool:
    oracle = _oracle()
    score, notes = oracle.score_rubric("too short")
    return score < 1.0 and any("length" in n for n in notes)


def test_rubric_long_text_full_score() -> bool:
    oracle = _oracle()
    score, notes = oracle.score_rubric(_clean_text(200))
    return score == 1.0


def test_rubric_receipts_required() -> bool:
    oracle = Oracle(rubric={**DEFAULT_RUBRIC, "min_receipts": 2})
    score, notes = oracle.score_rubric("no receipts here, just prose about stuff")
    return score < 1.0 and any("receipt" in n for n in notes)


def test_rubric_receipts_counted() -> bool:
    # Isolate receipts check from length penalty by setting min_length=0
    oracle = Oracle(rubric={**DEFAULT_RUBRIC, "min_receipts": 2, "min_length": 0})
    text = "Fix at brain_cli.py:42 and vault.py:100. Both tested."
    score, notes = oracle.score_rubric(text)
    return score == 1.0


def test_rubric_citations_required() -> bool:
    oracle = Oracle(rubric={**DEFAULT_RUBRIC, "require_citations": True})
    score, notes = oracle.score_rubric("no citations here at all")
    return score < 1.0 and any("citation" in n for n in notes)


def test_rubric_citations_present_https() -> bool:
    # Isolate citations check from length penalty
    oracle = Oracle(rubric={**DEFAULT_RUBRIC, "require_citations": True, "min_length": 0})
    text = "Per Anthropic MARS research: https://www.anthropic.com/engineering/mars"
    score, notes = oracle.score_rubric(text)
    return score == 1.0


# ── Theater detection ─────────────────────────────────────────────────────

def test_theater_version_tagged_flagged() -> bool:
    text = """
## Section A

Some normal content here with receipts at a.py:1.

## Upgrade Protocol (v4.1 — NEW)

""" + ("x " * 300) + """

No receipts in this block."""
    theater = _oracle().detect_theater(text)
    return any("version-tagged" in s["flags"] for s in theater.suspect_sections)


def test_theater_new_label_flagged() -> bool:
    text = """
## Real Section

Stuff at a.py:1.

### NEW Protocol

""" + ("foo bar " * 200)
    theater = _oracle().detect_theater(text)
    return any("labeled-NEW-or-UPGRADE" in s["flags"] for s in theater.suspect_sections)


def test_theater_expected_fire_rate_flagged() -> bool:
    text = """
## Feature Spec (v2.0)

This is a new protocol.

Expected fire rate: pending real data. We'll measure later.

Lots of filler content here without any receipts or concrete evidence.
"""
    theater = _oracle().detect_theater(text)
    return any("expected-fire-rate" in s["flags"] for s in theater.suspect_sections)


def test_theater_clean_content_kept() -> bool:
    text = """
## Real Receipts

- Finding at brain_cli.py:42
- Confirmed via decisions.jsonl grep
- Pattern reproduced in tests at test_vault.py:100

## Another Section

More evidence at mnemos.py:200 and oracle.py:50.
"""
    theater = _oracle().detect_theater(text)
    return theater.recommendation == "KEEP"


def test_theater_ratio_computation() -> bool:
    text = (
        "## Real\n\nReceipt at a.py:1.\n"
        + "## Fake (v1.0)\n\n"
        + ("x " * 500)  # long, no receipts
    )
    theater = _oracle().detect_theater(text)
    return theater.theater_ratio > 0.0


# ── Full score reports ────────────────────────────────────────────────────

def test_score_clean_passes() -> bool:
    text = _clean_text(300)
    report = _oracle().score(text)
    return report.verdict == "PASS"


def test_score_hard_fail_blocks() -> bool:
    # Unmitigated rule #2 violation → HARD_FAIL
    text = "I deleted the file without any backup or asking first."
    report = _oracle().score(text)
    return report.verdict == "HARD_FAIL" and report.overall_score < 0.8


def test_score_regression_detection() -> bool:
    baseline = "Clean output with receipts at a.py:1, b.py:2. All tests green."
    current = "Good enough output. Quick and dirty. Close enough for now."
    regs = _oracle().flag_regression(current, baseline)
    return len(regs) > 0


def test_score_no_false_positive_on_safe_delete_language() -> bool:
    text = (
        "Executed the 7 theater kills with explicit consent from the user. "
        "All files backed up first to Toke/hooks/deleted_20260411/. "
        "godspeed SKILL.md: 702 -> 540 lines (-162, -23%). "
        "Receipts at godspeed_SKILL_before_theater_kill.md."
    )
    report = _oracle().score(text)
    r2 = next(c for c in report.sacred_rule_checks if c.rule_id == "rule_2_no_delete")
    return r2.passed


# ── Sacred rule catalog completeness ──────────────────────────────────────

def test_sacred_rule_catalog_populated() -> bool:
    return len(SACRED_RULE_PATTERNS) >= 10


def test_every_rule_has_description() -> bool:
    return all("description" in spec for spec in SACRED_RULE_PATTERNS.values())


def test_every_rule_has_severity() -> bool:
    return all(spec["severity"] in ("hard", "soft") for spec in SACRED_RULE_PATTERNS.values())


TESTS = [
    # Sacred rules
    ("rule_2_unmitigated_delete_fails", test_rule_2_unmitigated_delete_fails),
    ("rule_2_mitigated_delete_passes", test_rule_2_mitigated_delete_passes),
    ("rule_3_revert_fails", test_rule_3_revert_fails),
    ("rule_4_scope_creep_fails", test_rule_4_scope_creep_fails),
    ("rule_5_diagnostic_removal_fails", test_rule_5_diagnostic_removal_fails),
    ("rule_6_creative_content_fails", test_rule_6_creative_content_fails),
    ("rule_13_aaa_violation_fails", test_rule_13_aaa_violation_fails),
    ("clean_text_passes_all_rules", test_clean_text_passes_all_rules),
    # Rubric
    ("rubric_short_text_penalized", test_rubric_short_text_penalized),
    ("rubric_long_text_full_score", test_rubric_long_text_full_score),
    ("rubric_receipts_required", test_rubric_receipts_required),
    ("rubric_receipts_counted", test_rubric_receipts_counted),
    ("rubric_citations_required", test_rubric_citations_required),
    ("rubric_citations_present_https", test_rubric_citations_present_https),
    # Theater
    ("theater_version_tagged_flagged", test_theater_version_tagged_flagged),
    ("theater_new_label_flagged", test_theater_new_label_flagged),
    ("theater_expected_fire_rate_flagged", test_theater_expected_fire_rate_flagged),
    ("theater_clean_content_kept", test_theater_clean_content_kept),
    ("theater_ratio_computation", test_theater_ratio_computation),
    # Full score
    ("score_clean_passes", test_score_clean_passes),
    ("score_hard_fail_blocks", test_score_hard_fail_blocks),
    ("score_regression_detection", test_score_regression_detection),
    ("score_no_false_positive_on_safe_delete_language", test_score_no_false_positive_on_safe_delete_language),
    # Catalog
    ("sacred_rule_catalog_populated", test_sacred_rule_catalog_populated),
    ("every_rule_has_description", test_every_rule_has_description),
    ("every_rule_has_severity", test_every_rule_has_severity),
]


def run_all() -> int:
    passed = 0
    failed = 0
    print("Homer ORACLE smoke tests")
    print("=" * 52)
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
    print("=" * 52)
    print(f"{passed}/{len(TESTS)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_all())
