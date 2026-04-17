#!/usr/bin/env python3
"""
Homer L6 — Sleep-time agent smoke tests
=======================================
Combined tests for Nyx, Hesper, Aurora using temp dirs.
All tests are offline (no API calls, no real skill scanning).
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

SLEEP_ROOT = Path(__file__).parent
HOMER_ROOT = SLEEP_ROOT.parent
ORACLE_PATH = HOMER_ROOT / "oracle"

# Load oracle + sleep agents
sys.path.insert(0, str(ORACLE_PATH))
sys.path.insert(0, str(SLEEP_ROOT / "nyx"))
sys.path.insert(0, str(SLEEP_ROOT / "hesper"))
sys.path.insert(0, str(SLEEP_ROOT / "aurora"))

import nyx  # noqa: E402
import hesper  # noqa: E402
import aurora  # noqa: E402
from oracle import Oracle  # noqa: E402

PASS = "[PASS]"
FAIL = "[FAIL]"


# ── Nyx tests ──────────────────────────────────────────────────────────────

def test_nyx_audit_on_empty_dir() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="nyx_test_"))
    try:
        result = nyx.run_audit(skills_dir=tmp)
        return result["ok"] is True and result["skills_audited"] == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_nyx_audit_finds_clean_skill() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="nyx_clean_"))
    try:
        skill_dir = tmp / "clean_skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "# Clean Skill\n\nReceipts at brain_cli.py:42. Use when you need X.\n",
            encoding="utf-8",
        )
        result = nyx.run_audit(skills_dir=tmp)
        return result["skills_audited"] == 1 and result["prune_candidates_count"] == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_nyx_audit_flags_theater_skill() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="nyx_theater_"))
    try:
        skill_dir = tmp / "theater_skill"
        skill_dir.mkdir()
        theater_content = (
            "# Theater Skill\n\n"
            "## Protocol (v4.1 — NEW)\n\n"
            + ("Long spec block with no receipts. " * 50)
            + "\n\nExpected fire rate: pending real data.\n"
        )
        (skill_dir / "SKILL.md").write_text(theater_content, encoding="utf-8")
        result = nyx.run_audit(skills_dir=tmp)
        return result["skills_audited"] == 1 and len(result["entries"][0]["suspect_sections"]) > 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_nyx_write_report_creates_file() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="nyx_report_"))
    try:
        # Build a synthetic report
        report = {
            "ok": True,
            "timestamp": "2026-04-11T14:00:00",
            "skills_audited": 3,
            "total_bytes": 15000,
            "prune_candidates_count": 1,
            "investigate_candidates_count": 2,
            "entries": [
                {"skill_name": "foo", "skill_md_path": "/x", "size_bytes": 5000,
                 "line_count": 100, "theater_ratio": 0.1, "theater_recommendation": "KEEP",
                 "suspect_sections": [], "learnings_entry_count": 5},
                {"skill_name": "bar", "skill_md_path": "/y", "size_bytes": 6000,
                 "line_count": 120, "theater_ratio": 0.4, "theater_recommendation": "PRUNE",
                 "suspect_sections": [{"header": "Dead", "line_start": 10, "line_end": 50,
                                       "body_length": 2000, "flags": ["version-tagged"]}],
                 "learnings_entry_count": 0},
                {"skill_name": "baz", "skill_md_path": "/z", "size_bytes": 4000,
                 "line_count": 90, "theater_ratio": 0.2, "theater_recommendation": "INVESTIGATE",
                 "suspect_sections": [], "learnings_entry_count": 2},
            ],
            "prune_candidates": [],
            "investigate_candidates": [],
        }
        # Populate from filtered lists
        report["prune_candidates"] = [e for e in report["entries"] if e["theater_recommendation"] == "PRUNE"]
        report["investigate_candidates"] = [e for e in report["entries"] if e["theater_recommendation"] == "INVESTIGATE"]

        path = nyx.write_report(report, reports_dir=tmp)
        return path.exists() and path.read_text(encoding="utf-8").startswith("# Nyx Theater Audit")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Hesper tests ───────────────────────────────────────────────────────────

def test_hesper_parse_learning_file_extracts_entries() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="hesper_parse_"))
    try:
        learning = tmp / "_learnings.md"
        learning.write_text(
            "# Skill Learnings\n\n"
            "### PATTERN: Something important — 2026-04-10\n"
            '<!-- meta: { "roi_score": 5, "confidence": "HIGH", "confirmed_count": 3 } -->\n'
            "Body of the pattern.\n\n"
            "### Note: Minor observation — 2026-04-11\n"
            "Just a note.\n",
            encoding="utf-8",
        )
        entries = hesper.parse_learning_file(learning, skill_name="test_skill")
        return (
            len(entries) == 2
            and entries[0].roi == 5
            and entries[0].confidence == "HIGH"
            and entries[0].confirmed_count == 3
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_hesper_composite_score() -> bool:
    entry = hesper.LearningEntry(
        title="x", date="2026-04-11", source_path="/x", skill="s",
        roi=5, confidence="HIGH", confirmed_count=3,
    )
    # 5 * 3 * 3 = 45
    return entry.score == 45


def test_hesper_distill_ranks_by_score() -> bool:
    entries = [
        hesper.LearningEntry(title="low", date="", source_path="", skill="s",
                             roi=1, confidence="LOW", confirmed_count=1),
        hesper.LearningEntry(title="high", date="", source_path="", skill="s",
                             roi=5, confidence="HIGH", confirmed_count=5),
        hesper.LearningEntry(title="med", date="", source_path="", skill="s",
                             roi=3, confidence="MEDIUM", confirmed_count=2),
    ]
    top = hesper.distill(entries, top_n=3)
    return top[0].title == "high" and top[-1].title == "low"


def test_hesper_mine_empty_dir() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="hesper_mine_"))
    try:
        entries = hesper.mine_all_sources(skills_dir=tmp)
        # Empty skills dir, shared learnings may or may not exist — either way, should not crash
        return isinstance(entries, list)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_hesper_write_best_practices() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="hesper_bp_"))
    try:
        entries = [
            hesper.LearningEntry(
                title="Test Pattern",
                date="2026-04-11",
                source_path="/fake/path.md",
                skill="test_skill",
                roi=4, confidence="HIGH", confirmed_count=2,
                body_excerpt="This is the body excerpt",
            )
        ]
        path = hesper.write_best_practices(entries, total_mined=10, reports_dir=tmp)
        content = path.read_text(encoding="utf-8")
        return "Test Pattern" in content and "High" in content or "HIGH" in content
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Aurora tests ───────────────────────────────────────────────────────────

def test_aurora_analyze_missing_file() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="aurora_miss_"))
    try:
        dist = aurora.analyze_decisions(tmp / "nonexistent.jsonl")
        return dist.total == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_aurora_analyze_real_jsonl() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="aurora_real_"))
    try:
        jsonl = tmp / "decisions.jsonl"
        rows = [
            {"result": {"tier": "S0", "model": "haiku", "confidence": 0.9,
                         "uncertainty_escalated": False, "guardrails_fired": []}},
            {"result": {"tier": "S5", "model": "opus[1m]", "confidence": 0.4,
                         "uncertainty_escalated": True,
                         "guardrails_fired": ["gpqa_hard_reasoning"]}},
            {"result": {"tier": "S1", "model": "haiku", "confidence": 0.6,
                         "uncertainty_escalated": False,
                         "correction_detected_in_prompt": True,
                         "guardrails_fired": []}},
        ]
        jsonl.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        dist = aurora.analyze_decisions(jsonl)
        return (
            dist.total == 3
            and dist.by_tier.get("S0") == 1
            and dist.by_tier.get("S5") == 1
            and dist.by_model.get("haiku") == 2
            and dist.uncertainty_escalated_count == 1
            and dist.corrections_detected == 1
            and dist.guardrails_fired.get("gpqa_hard_reasoning") == 1
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_aurora_propose_raise_tier_floor_on_high_uncertainty() -> bool:
    dist = aurora.TierDistribution(
        total=100,
        by_tier={"S3": 100},
        by_model={"sonnet": 100},
        uncertainty_escalated_count=50,  # 50% — above 40% threshold
    )
    proposals = aurora.propose_weight_adjustments(dist)
    return any(p["id"] == "raise_tier_floor" for p in proposals)


def test_aurora_propose_on_correction_rate() -> bool:
    dist = aurora.TierDistribution(
        total=100,
        by_tier={"S2": 100},
        by_model={"sonnet": 100},
        corrections_detected=10,  # 10% — above 5% threshold
    )
    proposals = aurora.propose_weight_adjustments(dist)
    return any(p["id"] == "bump_escalation_weights" for p in proposals)


def test_aurora_no_proposals_on_clean_data() -> bool:
    dist = aurora.TierDistribution(
        total=100,
        by_tier={"S2": 50, "S3": 50},
        by_model={"sonnet": 100},
        uncertainty_escalated_count=5,  # 5% — below 40%
        corrections_detected=1,         # 1% — below 5%
        avg_confidence=0.8,
    )
    proposals = aurora.propose_weight_adjustments(dist)
    return len(proposals) == 0


def test_aurora_propose_dead_guardrail() -> bool:
    dist = aurora.TierDistribution(
        total=150,
        by_tier={"S1": 150},
        by_model={"haiku": 150},
        guardrails_fired={"never_used_guardrail": 0},  # zero fires over 150 decisions
        avg_confidence=0.8,
    )
    proposals = aurora.propose_weight_adjustments(dist)
    return any("dead_guardrail" in p["id"] for p in proposals)


def test_aurora_run_tuning_writes_file() -> bool:
    tmp = Path(tempfile.mkdtemp(prefix="aurora_run_"))
    try:
        # Fake decisions file
        jsonl = tmp / "decisions.jsonl"
        rows = [{"result": {"tier": "S1", "model": "haiku", "confidence": 0.8,
                             "uncertainty_escalated": False, "guardrails_fired": []}}]
        jsonl.write_text(json.dumps(rows[0]), encoding="utf-8")

        result = aurora.run_tuning(decisions_path=jsonl, proposals_dir=tmp / "proposals")
        report_path = Path(result["report_path"])
        return result["ok"] and report_path.exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Integration ────────────────────────────────────────────────────────────

def test_sleep_agents_importable() -> bool:
    return hasattr(nyx, "run_audit") and hasattr(hesper, "run_distillation") and hasattr(aurora, "run_tuning")


def test_oracle_integration() -> bool:
    oracle = Oracle()
    # Theater detection should work (nyx depends on it)
    text = "## Feature (v4.1 — NEW)\n\n" + ("x " * 300) + "\nExpected fire rate: pending."
    report = oracle.detect_theater(text)
    return len(report.suspect_sections) > 0


TESTS = [
    # Nyx
    ("nyx_audit_on_empty_dir", test_nyx_audit_on_empty_dir),
    ("nyx_audit_finds_clean_skill", test_nyx_audit_finds_clean_skill),
    ("nyx_audit_flags_theater_skill", test_nyx_audit_flags_theater_skill),
    ("nyx_write_report_creates_file", test_nyx_write_report_creates_file),
    # Hesper
    ("hesper_parse_learning_file_extracts_entries", test_hesper_parse_learning_file_extracts_entries),
    ("hesper_composite_score", test_hesper_composite_score),
    ("hesper_distill_ranks_by_score", test_hesper_distill_ranks_by_score),
    ("hesper_mine_empty_dir", test_hesper_mine_empty_dir),
    ("hesper_write_best_practices", test_hesper_write_best_practices),
    # Aurora
    ("aurora_analyze_missing_file", test_aurora_analyze_missing_file),
    ("aurora_analyze_real_jsonl", test_aurora_analyze_real_jsonl),
    ("aurora_propose_raise_tier_floor_on_high_uncertainty", test_aurora_propose_raise_tier_floor_on_high_uncertainty),
    ("aurora_propose_on_correction_rate", test_aurora_propose_on_correction_rate),
    ("aurora_no_proposals_on_clean_data", test_aurora_no_proposals_on_clean_data),
    ("aurora_propose_dead_guardrail", test_aurora_propose_dead_guardrail),
    ("aurora_run_tuning_writes_file", test_aurora_run_tuning_writes_file),
    # Integration
    ("sleep_agents_importable", test_sleep_agents_importable),
    ("oracle_integration", test_oracle_integration),
]


def run_all() -> int:
    passed = 0
    failed = 0
    print("Homer SLEEP-TIME smoke tests (Nyx + Hesper + Aurora)")
    print("=" * 56)
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
    print("=" * 56)
    print(f"{passed}/{len(TESTS)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_all())
