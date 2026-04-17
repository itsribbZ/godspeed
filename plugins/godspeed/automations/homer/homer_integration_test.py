#!/usr/bin/env python3
"""
Homer P4 — Integration Test
============================
Proves all Python-callable Homer layers coordinate in one flow:
  L0 VAULT   → checkpoint create + read + phase transition
  L4 SYBIL   → precondition check (dry-run, no real API call)
  L5 MNEMOS  → Core write (citation enforced) + Recall write + search
  L6 SLEEP   → Nyx audit + Hesper distill + Aurora analyze (on test data)
  L7 ORACLE  → sacred rules score + theater detection

L1 Brain, L2 Zeus, L3 MUSES are Claude-dispatched skills — they require
Claude Code to invoke and can't be integration-tested from Python alone.
Their SKILL.md files are verified as installed.

This test uses temp dirs where possible to avoid polluting real state.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

HOMER_ROOT = Path(__file__).parent
SKILLS_DIR = Path.home() / ".claude" / "skills"

# Add all Homer module paths
sys.path.insert(0, str(HOMER_ROOT / "vault"))
sys.path.insert(0, str(HOMER_ROOT / "sybil"))
sys.path.insert(0, str(HOMER_ROOT / "mnemos"))
sys.path.insert(0, str(HOMER_ROOT / "oracle"))
sys.path.insert(0, str(HOMER_ROOT / "sleep" / "nyx"))
sys.path.insert(0, str(HOMER_ROOT / "sleep" / "hesper"))
sys.path.insert(0, str(HOMER_ROOT / "sleep" / "aurora"))
sys.path.insert(0, str(HOMER_ROOT / "zeus"))
sys.path.insert(0, str(HOMER_ROOT / "muses"))
sys.path.insert(0, str(HOMER_ROOT / "muses" / "calliope"))
sys.path.insert(0, str(HOMER_ROOT / "muses" / "clio"))
sys.path.insert(0, str(HOMER_ROOT / "muses" / "urania"))

from vault import VaultStore  # noqa: E402
from sybil import check_preconditions  # noqa: E402
from mnemos import MnemosStore, CitationError  # noqa: E402
from oracle import Oracle  # noqa: E402
import nyx  # noqa: E402
import hesper  # noqa: E402
from zeus_pipeline import gate_and_write  # noqa: E402 (G3 2026-04-17)
import calliope  # noqa: E402 (G2 2026-04-17)
import clio  # noqa: E402
import urania  # noqa: E402
import aurora  # noqa: E402

PASS = "[PASS]"
FAIL = "[FAIL]"


def integration_vault_checkpoint_lifecycle() -> bool:
    """L0: Create → phase transition → read back."""
    tmp = Path(tempfile.mkdtemp(prefix="homer_int_vault_"))
    try:
        store = VaultStore(state_dir=tmp)
        cp = store.create(topic="integration_test", session_id="int_test_001", phase="init")
        assert cp.phase == "init"
        cp.phase = "plan"
        store.update(cp)
        cp.phase = "dispatch"
        store.update(cp)
        cp.phase = "done"
        store.update(cp)
        reread = store.read(cp.checkpoint_id)
        return reread is not None and reread.phase == "done"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def integration_sybil_preconditions() -> bool:
    """L4: Precondition check runs without crashing."""
    check = check_preconditions(session_id="int_test", task_text="test task")
    return hasattr(check, "overall_pass") and isinstance(check.failure_reasons, list)


def integration_sybil_creative_block() -> bool:
    """L4: Creative content is blocked."""
    check = check_preconditions(
        session_id="int_test_creative",
        task_text="write dialogue for the boss fight",
    )
    return not check.not_creative_content


def integration_mnemos_core_with_citation() -> bool:
    """L5: Write to Core with citation enforcement."""
    tmp = Path(tempfile.mkdtemp(prefix="homer_int_mnemos_"))
    try:
        store = MnemosStore(
            core_file=tmp / "core" / "core.md",
            recall_db=tmp / "recall" / "recall.db",
            archival_dir=tmp / "archival",
        )
        entry = store.write_core(
            pattern="Integration test: VAULT → MNEMOS → ORACLE coordination verified",
            citation="homer_integration_test.py:1",
            confidence="HIGH",
        )
        entries = store.read_core()
        return len(entries) == 1 and entries[0]["citation"] == "homer_integration_test.py:1"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def integration_mnemos_citation_enforcement() -> bool:
    """L5: Invalid citation is rejected."""
    tmp = Path(tempfile.mkdtemp(prefix="homer_int_mnemos_rej_"))
    try:
        store = MnemosStore(
            core_file=tmp / "core" / "core.md",
            recall_db=tmp / "recall" / "recall.db",
            archival_dir=tmp / "archival",
        )
        try:
            store.write_core(pattern="bad write", citation="vibes only", confidence="HIGH")
            return False
        except CitationError:
            return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def integration_mnemos_recall_search() -> bool:
    """L5: Write to Recall + search finds it."""
    tmp = Path(tempfile.mkdtemp(prefix="homer_int_recall_"))
    try:
        store = MnemosStore(
            core_file=tmp / "core" / "core.md",
            recall_db=tmp / "recall" / "recall.db",
            archival_dir=tmp / "archival",
        )
        store.write_recall(
            topic="Homer P4 integration",
            content="all 8 layers coordinating in one integration test run",
            citations=["homer_integration_test.py:1"],
        )
        results = store.search_recall("integration", limit=5)
        return len(results) >= 1 and "integration" in results[0].get("content", "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def integration_oracle_sacred_rules() -> bool:
    """L7: Oracle scores clean text as PASS."""
    oracle = Oracle()
    report = oracle.score("Shipped Homer P3 with 104 tests green. Receipts at oracle.py:42 and vault.py:100.")
    return report.verdict == "PASS"


def integration_oracle_hard_fail() -> bool:
    """L7: Oracle catches unmitigated deletion."""
    oracle = Oracle()
    report = oracle.score("I deleted the entire codebase and removed all diagnostics.")
    return report.verdict == "HARD_FAIL"


def integration_oracle_theater_detection() -> bool:
    """L7: Oracle detects theater in version-tagged spec with no receipts."""
    oracle = Oracle()
    text = (
        "## Working Section\n\nReceipts at vault.py:42.\n"
        "## Speculative Protocol (v4.1 — NEW)\n\n"
        + ("Long spec with no evidence. " * 30)
        + "\nExpected fire rate: pending real data.\n"
    )
    theater = oracle.detect_theater(text)
    # With 2+ flags requirement, this should still flag (version-tagged + no-receipts + expected-fire-rate)
    return len(theater.suspect_sections) >= 1


def integration_nyx_runs_on_real_skills() -> bool:
    """L6-Nyx: Audit runs against real ~/.claude/skills/ without crashing."""
    report = nyx.run_audit()
    return report["ok"] and report["skills_audited"] > 0


def integration_aurora_analyzes_real_decisions() -> bool:
    """L6-Aurora: Analyze real decisions.jsonl."""
    dist = aurora.analyze_decisions()
    return dist.total > 0


def integration_hesper_parses_real_learnings() -> bool:
    """L6-Hesper: Mine real skill learnings."""
    entries = hesper.mine_all_sources()
    return isinstance(entries, list) and len(entries) > 0


def integration_all_homer_skills_installed() -> bool:
    """P4: Verify all 10 Homer SKILL.md files exist in ~/.claude/skills/."""
    expected = ["zeus", "mnemos", "calliope", "clio", "urania", "oracle",
                "nyx", "hesper", "aurora", "sybil"]
    for skill in expected:
        path = SKILLS_DIR / skill / "SKILL.md"
        if not path.exists():
            return False
    return True


def integration_vault_to_mnemos_to_oracle_pipeline() -> bool:
    """Full pipeline: create checkpoint → write to Mnemos → score with Oracle → update checkpoint."""
    tmp = Path(tempfile.mkdtemp(prefix="homer_int_pipeline_"))
    try:
        # L0: VAULT — create checkpoint
        vault = VaultStore(state_dir=tmp / "vault")
        cp = vault.create(
            topic="pipeline_test",
            session_id="pipeline_001",
            phase="init",
            tasks=[{"id": 1, "priority": "P0", "status": "pending", "description": "integration test"}],
        )
        cp.phase = "dispatch"
        vault.update(cp)

        # L5: MNEMOS — write a finding with citation
        mnemos = MnemosStore(
            core_file=tmp / "mnemos" / "core" / "core.md",
            recall_db=tmp / "mnemos" / "recall" / "recall.db",
            archival_dir=tmp / "mnemos" / "archival",
        )
        entry = mnemos.write_core(
            pattern="VAULT + MNEMOS + ORACLE coordinate in pipeline test",
            citation="homer_integration_test.py:1",
            confidence="HIGH",
        )
        mnemos.write_recall(
            topic="pipeline integration",
            content="All layers fired in sequence",
            citations=["homer_integration_test.py:1"],
        )

        # L7: ORACLE — score the finding
        oracle = Oracle()
        report = oracle.score(entry.pattern + f" Citation: {entry.citation}")
        assert report.verdict in ("PASS", "SOFT_FAIL")

        # L0: VAULT — finalize checkpoint
        cp.phase = "done"
        cp.tasks[0]["status"] = "done"
        vault.update(cp)
        final = vault.read(cp.checkpoint_id)
        return (
            final is not None
            and final.phase == "done"
            and final.tasks[0]["status"] == "done"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def integration_muses_python_contract() -> bool:
    """G2 (2026-04-17): every MUSE has a Python-level run() interface Zeus can call."""
    # Stub path: no executor, returns safe placeholder with correct shape
    for muse_mod, name in [(calliope, "calliope"), (clio, "clio"), (urania, "urania")]:
        r = muse_mod.run(f"smoke test task for {name}")
        if r.muse != name:
            return False
        if not r.output:
            return False
        if r.error is not None:
            return False

    # Mocked executor path: caller injects a deterministic runner
    def fake_exec(prompt: str, context: dict) -> str:
        return f"[mocked output] prompt_len={len(prompt)}"
    r = calliope.run("real task", {"k": "v"}, executor=fake_exec)
    return r.roi == 3 and r.output.startswith("[mocked output]")


def integration_zeus_oracle_gate_blocks_hard_fail() -> bool:
    """G3 (2026-04-17): Oracle HARD_FAIL MUST block the Mnemos write."""
    tmp = Path(tempfile.mkdtemp(prefix="homer_g3_"))
    try:
        store = MnemosStore(
            core_file=tmp / "core.md",
            recall_db=tmp / "recall.db",
            archival_dir=tmp / "archival",
        )
        oracle = Oracle()
        # Sacred rule 2 hard-fail pattern: "deleted ... without consent"
        bad = ("I deleted the settings.json and overwrote the config files without consent. "
               "Then ran rm -rf on the cache dir. ") * 5
        result = gate_and_write(
            oracle, store, bad, "g3_block_test", ["session:g3_test_20260417"]
        )
        return (not result.written) and result.verdict == "HARD_FAIL" and bool(result.rule_failures)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def integration_zeus_oracle_gate_passes_clean() -> bool:
    """G3 (2026-04-17): Oracle PASS allows Mnemos write with entry_id returned."""
    tmp = Path(tempfile.mkdtemp(prefix="homer_g3_pass_"))
    try:
        store = MnemosStore(
            core_file=tmp / "core.md",
            recall_db=tmp / "recall.db",
            archival_dir=tmp / "archival",
        )
        oracle = Oracle()
        good = ("## Synthesis\n\nBrain classifier routes prompts to S0-S5 tiers. "
                "See decisions.jsonl:100 for receipts. ") * 3
        result = gate_and_write(
            oracle, store, good, "g3_pass_test", ["session:g3_test_20260417"]
        )
        return result.written and result.verdict == "PASS" and result.entry_id is not None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def integration_zeus_cli_status_returns_json() -> bool:
    """Zeus CLI status subcommand emits valid JSON with expected keys."""
    import subprocess
    tmp = Path(tempfile.mkdtemp(prefix="zeus_cli_status_"))
    try:
        cli = HOMER_ROOT / "zeus" / "zeus_cli.py"
        proc = subprocess.run(
            [sys.executable, str(cli), "status",
             "--core-file", str(tmp / "core.md"),
             "--recall-db", str(tmp / "recall.db"),
             "--archival-dir", str(tmp / "archival")],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        if proc.returncode != 0:
            return False
        payload = json.loads(proc.stdout)
        return (
            "oracle_loaded" in payload
            and "mnemos" in payload
            and "brain_cli_present" in payload
            and "zeus_pipeline_loaded" in payload
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def integration_zeus_cli_gate_write_pass_writes_entry() -> bool:
    """Zeus CLI gate-write end-to-end: clean synthesis writes an isolated Mnemos row and returns exit 0."""
    import subprocess
    tmp = Path(tempfile.mkdtemp(prefix="zeus_cli_pass_"))
    try:
        synthesis = tmp / "synth.md"
        synthesis.write_text(
            "## Zeus Synthesis\n\nBrain classifier routes prompts to S0-S5 tiers per "
            "decisions.jsonl:100. Receipts verified. ",
            encoding="utf-8",
        )
        cli = HOMER_ROOT / "zeus" / "zeus_cli.py"
        proc = subprocess.run(
            [sys.executable, str(cli), "gate-write",
             "--topic", "Zeus CLI integration test",
             "--synthesis-file", str(synthesis),
             "--citations", "decisions.jsonl:100,session:zeus_cli_it_20260417",
             "--core-file", str(tmp / "core.md"),
             "--recall-db", str(tmp / "recall.db"),
             "--archival-dir", str(tmp / "archival")],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        if proc.returncode != 0:
            print(f"    DEBUG stdout: {proc.stdout[:200]}", file=sys.stderr)
            print(f"    DEBUG stderr: {proc.stderr[:200]}", file=sys.stderr)
            return False
        payload = json.loads(proc.stdout)
        return (
            payload.get("written") is True
            and payload.get("verdict") == "PASS"
            and payload.get("entry_id", "").startswith("recall_")
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def integration_zeus_cli_gate_write_citation_rejected() -> bool:
    """Zeus CLI rejects a vague citation with exit code 2 and structured error."""
    import subprocess
    tmp = Path(tempfile.mkdtemp(prefix="zeus_cli_citerr_"))
    try:
        synthesis = tmp / "synth.md"
        synthesis.write_text("Clean synthesis with proper receipts. " * 5, encoding="utf-8")
        cli = HOMER_ROOT / "zeus" / "zeus_cli.py"
        proc = subprocess.run(
            [sys.executable, str(cli), "gate-write",
             "--topic", "Zeus CLI citation test",
             "--synthesis-file", str(synthesis),
             "--citations", "around line 50",  # vague — must be rejected
             "--core-file", str(tmp / "core.md"),
             "--recall-db", str(tmp / "recall.db"),
             "--archival-dir", str(tmp / "archival")],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        if proc.returncode != 2:
            return False
        payload = json.loads(proc.stdout)
        return (
            payload.get("written") is False
            and payload.get("verdict") == "MNEMOS_CITATION_REJECTED"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


TESTS = [
    ("vault_checkpoint_lifecycle", integration_vault_checkpoint_lifecycle),
    ("sybil_preconditions", integration_sybil_preconditions),
    ("sybil_creative_block", integration_sybil_creative_block),
    ("mnemos_core_with_citation", integration_mnemos_core_with_citation),
    ("mnemos_citation_enforcement", integration_mnemos_citation_enforcement),
    ("mnemos_recall_search", integration_mnemos_recall_search),
    ("oracle_sacred_rules", integration_oracle_sacred_rules),
    ("oracle_hard_fail", integration_oracle_hard_fail),
    ("oracle_theater_detection", integration_oracle_theater_detection),
    ("nyx_runs_on_real_skills", integration_nyx_runs_on_real_skills),
    ("aurora_analyzes_real_decisions", integration_aurora_analyzes_real_decisions),
    ("hesper_parses_real_learnings", integration_hesper_parses_real_learnings),
    ("all_homer_skills_installed", integration_all_homer_skills_installed),
    ("vault_to_mnemos_to_oracle_pipeline", integration_vault_to_mnemos_to_oracle_pipeline),
    ("muses_python_contract", integration_muses_python_contract),
    ("zeus_oracle_gate_blocks_hard_fail", integration_zeus_oracle_gate_blocks_hard_fail),
    ("zeus_oracle_gate_passes_clean", integration_zeus_oracle_gate_passes_clean),
    ("zeus_cli_status_returns_json", integration_zeus_cli_status_returns_json),
    ("zeus_cli_gate_write_pass_writes_entry", integration_zeus_cli_gate_write_pass_writes_entry),
    ("zeus_cli_gate_write_citation_rejected", integration_zeus_cli_gate_write_citation_rejected),
]


def run_all() -> int:
    passed = 0
    failed = 0
    print("Homer P4 — INTEGRATION TEST (cross-layer coordination)")
    print("=" * 60)
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
    print("=" * 60)
    print(f"{passed}/{len(TESTS)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_all())
